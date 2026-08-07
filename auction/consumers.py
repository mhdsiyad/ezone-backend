import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone

# In-memory spectator tracking: { group_name: set(channel_names) }
_spectators: dict = {}

# Track which teams have an active captain WebSocket.
# Structure: { group_name: { team_id: channel_name } }
# Keyed by channel_name so disconnect() can safely skip removal when a
# page-refresh reconnect has already registered a fresh channel for the team.
_online_teams: dict = {}


class AuctionConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time auction events.
    Group name: auction_{auction_id}

    Server → Client events:
        auction_state, player_update, bid_update, timer_update, auction_status,
        bid_countdown, teams_update, auction_end, spectator_count, teams_online,
        roster_update

    Client → Server events:
        place_bid, ping
    """

    async def connect(self):
        self.auction_id = self.scope['url_route']['kwargs']['auction_id']
        self.group_name = f'auction_{self.auction_id}'
        self.online_team_id = None  # set below if captain

        # Join the auction channel group
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Track spectator count
        if self.group_name not in _spectators:
            _spectators[self.group_name] = set()
        _spectators[self.group_name].add(self.channel_name)

        await self.channel_layer.group_send(self.group_name, {
            'type': 'spectator_count',
            'count': len(_spectators[self.group_name]),
        })

        # Track captain's team as online
        user = self.scope.get('user')
        if user and user.is_authenticated and getattr(user, 'role', None) == 'captain':
            team_id = await self._get_team_id(user)
            if team_id:
                self.online_team_id = team_id
                if self.group_name not in _online_teams:
                    _online_teams[self.group_name] = {}
                # Register this channel as the active WS for the team.
                # If the captain reconnects, the new channel_name overwrites the
                # old one, which lets disconnect() detect a stale removal.
                _online_teams[self.group_name][team_id] = self.channel_name
                await self.channel_layer.group_send(self.group_name, {
                    'type': 'teams_online',
                    'team_ids': list(_online_teams.get(self.group_name, {}).keys()),
                })

        # Send current auction state on connect
        state = await self.get_auction_state()
        if state:
            await self.send(text_data=json.dumps({
                'type': 'auction_state',
                'data': state
            }))

        # Immediately push current teams_online snapshot to this specific client
        # (they may have connected after captains were already online)
        online_ids = list(_online_teams.get(self.group_name, {}).keys())
        await self.send(text_data=json.dumps({
            'type': 'teams_online',
            'team_ids': online_ids,
        }))

    async def disconnect(self, close_code):
        # Remove from spectator tracking with a short debounce to avoid
        # reconnect flicker (React Strict Mode / auto-reconnect causes brief double-connect)
        if self.group_name in _spectators:
            _spectators[self.group_name].discard(self.channel_name)
            count = len(_spectators[self.group_name])
            if count == 0:
                _spectators.pop(self.group_name, None)
            # Debounce: wait briefly before broadcasting the lower count
            await asyncio.sleep(1.5)
            # Re-check count after the delay (new connection may have joined)
            final_count = len(_spectators.get(self.group_name, set()))
            try:
                await self.channel_layer.group_send(self.group_name, {
                    'type': 'spectator_count',
                    'count': final_count,
                })
            except Exception:
                pass  # group may no longer exist

        # Remove captain's team — reconnect-safe guard.
        #
        # After the 1.5s spectator sleep above, a page-refresh may have already
        # run connect() and registered a NEW channel_name for the same team.
        # We only remove the entry if OUR channel_name is still the registered
        # one; if it was replaced, we leave it alone so the LIVE badge persists.
        if self.online_team_id is not None:
            group_teams = _online_teams.get(self.group_name, {})
            if group_teams.get(self.online_team_id) == self.channel_name:
                group_teams.pop(self.online_team_id, None)
                if not group_teams:
                    _online_teams.pop(self.group_name, None)
                remaining = list(_online_teams.get(self.group_name, {}).keys())
                try:
                    await self.channel_layer.group_send(self.group_name, {
                        'type': 'teams_online',
                        'team_ids': remaining,
                    })
                except Exception:
                    pass
            # else: a newer reconnect already registered — don't touch it

        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        event_type = data.get('type')

        if event_type == 'place_bid':
            await self.handle_bid(data)
        elif event_type == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))

    async def handle_bid(self, data):
        """Processes an incoming bid request and broadcasts updates."""
        try:
            user = self.scope['user']
            if not user.is_authenticated:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'You must be logged in to bid.'
                }))
                return

            if user.role != 'captain':
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Only team captains can place bids.'
                }))
                return

            amount = data.get('amount')
            if not amount or not isinstance(amount, (int, float)) or amount <= 0:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Invalid bid amount.'
                }))
                return

            result = await self.save_bid(user, int(amount))

            if result.get('error'):
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': result['error']
                }))
                return

            # Broadcast bid to all in the group
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'bid_update',
                    'bid': result['bid']
                }
            )

            # Force status to active in case _handle_timer_expired raced and marked it pending
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'auction_status',
                    'status': 'active'
                }
            )

            from auction.views import _active_countdowns, _cancel_countdown, _start_timer
            
            # If a 3-2-1 countdown was running, this bid cancels it
            countdown_cancelled = False
            if _active_countdowns.get(self.auction_id):
                _cancel_countdown(self.auction_id)
                await self.channel_layer.group_send(self.group_name, {
                    'type': 'bid_countdown', 'value': None
                })
                countdown_cancelled = True

            # If timer < 10s, extend and broadcast updated timer.
            if result.get('timer_extended'):
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'timer_update',
                        'timeLeft': result['new_timer']
                    }
                )
                _start_timer(self.auction_id)
            elif countdown_cancelled:
                # If we cancelled a countdown but timer was already >= 10, we still need to restart the timer thread
                _start_timer(self.auction_id)
        except Exception:
            pass

    # ── Database helpers ────────────────────────────────────────────────────

    @database_sync_to_async
    def _get_team_id(self, user):
        """Look up the team where captain_username matches the connected user's username."""
        from .models import Team
        try:
            team = Team.objects.get(captain_username=user.username)
            return team.id
        except Team.DoesNotExist:
            return None
        except Exception:
            return None

    @database_sync_to_async
    def get_auction_state(self):
        from .models import Auction, Bid, AuctionTeam, SoldResult
        from .serializers import (
            PlayerSerializer, BidSerializer, AuctionTeamSerializer
        )
        try:
            auction = Auction.objects.get(id=self.auction_id)
        except Auction.DoesNotExist:
            return None

        current_player_data = None
        highest_bid_data = None

        if auction.current_player:
            current_player_data = PlayerSerializer(auction.current_player).data
            top_bid = Bid.objects.filter(
                auction=auction, player=auction.current_player
            ).order_by('-amount').first()
            if top_bid:
                highest_bid_data = BidSerializer(top_bid).data

        auction_teams = AuctionTeam.objects.filter(auction=auction).select_related('team')
        teams_data = AuctionTeamSerializer(auction_teams, many=True).data

        recent_bids = Bid.objects.filter(auction=auction).select_related('team')[:20]
        bids_data = BidSerializer(recent_bids, many=True).data

        return {
            'auction_id': auction.id,
            'status': auction.status,
            'current_timer': auction.current_timer,
            'current_player': current_player_data,
            'highest_bid': highest_bid_data,
            'teams': teams_data,
            'recent_bids': bids_data,
        }

    @database_sync_to_async
    def save_bid(self, user, amount):
        from .models import Auction, AuctionTeam, Bid, Player, SoldResult
        from .serializers import BidSerializer
        from django.db import transaction

        with transaction.atomic():
            try:
                auction = Auction.objects.select_for_update().get(id=self.auction_id)
            except Auction.DoesNotExist:
                return {'error': 'Auction not found.'}

            if auction.status != 'active':
                return {'error': 'Auction is not currently active.'}

            if not auction.current_player:
                return {'error': 'No player is currently up for auction.'}

            # Get team via captain's linked team
            try:
                team = user.team
            except Exception:
                return {'error': 'Captain has no team assigned.'}

            # Verify team is part of this auction
            try:
                auction_team = AuctionTeam.objects.get(auction=auction, team=team)
            except AuctionTeam.DoesNotExist:
                return {'error': 'Your team is not part of this auction.'}

            # Get current highest bid
            top_bid = Bid.objects.filter(
                auction=auction, player=auction.current_player
            ).order_by('-amount').first()

            floor = top_bid.amount if top_bid else auction.current_player.base_price

            # The opening bid on a player may match the base price exactly; every bid
            # after that must strictly raise it.
            if (amount < floor) if not top_bid else (amount <= floor):
                return {'error': f'Bid must be at least ${floor}.' if not top_bid else f'Bid must be greater than ${floor}.'}

            if amount > auction_team.balance:
                return {'error': f'Insufficient balance. You have ${auction_team.balance}.'}

            from .views import price_locked_max_bid
            won_count = SoldResult.objects.filter(auction=auction, team=team).count()
            max_locked_bid = price_locked_max_bid(auction, auction_team, won_count)
            if max_locked_bid is not None and amount > max_locked_bid:
                return {'error': f'Price Lock: max bid is ${max_locked_bid} — you must reserve enough balance for your remaining roster slots.'}

            # Save bid
            bid = Bid.objects.create(
                auction=auction,
                player=auction.current_player,
                team=team,
                amount=amount
            )
            bid.refresh_from_db()

            # Extend timer if < 10s and force status to active to fix race condition with _handle_timer_expired
            timer_extended = False
            new_timer = auction.current_timer
            update_fields = ['status']
            auction.status = 'active'
            
            if auction.current_timer < 10:
                auction.current_timer = 10
                update_fields.append('current_timer')
                timer_extended = True
                new_timer = 10
                
            auction.save(update_fields=update_fields)

            return {
                'bid': {
                    'id': bid.id,
                    'team_id': team.id,
                    'team_name': team.name,
                    'amount': bid.amount,
                    'timestamp': bid.timestamp.isoformat(),
                },
                'timer_extended': timer_extended,
                'new_timer': new_timer,
            }

    # ── Channel layer message handlers (server → client broadcasts) ─────────

    async def bid_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'bid_update',
            'bid': event['bid']
        }))

    async def player_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'player_update',
            'player': event['player'],
            'timer': event.get('timer', 0),
        }))

    async def timer_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'timer_update',
            'timeLeft': event['timeLeft']
        }))

    async def auction_status(self, event):
        await self.send(text_data=json.dumps({
            'type': 'auction_status',
            'status': event['status']
        }))

    async def bid_countdown(self, event):
        await self.send(text_data=json.dumps({
            'type': 'bid_countdown',
            'value': event['value']
        }))

    async def teams_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'teams_update',
            'teams': event['teams']
        }))

    async def auction_end(self, event):
        await self.send(text_data=json.dumps({
            'type': 'auction_end',
            'results': event['results']
        }))

    async def spectator_count(self, event):
        await self.send(text_data=json.dumps({
            'type': 'spectator_count',
            'count': event['count'],
        }))

    async def teams_online(self, event):
        await self.send(text_data=json.dumps({
            'type': 'teams_online',
            'team_ids': event['team_ids'],
        }))

    async def roster_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'roster_update',
            'players': event['players'],
        }))
