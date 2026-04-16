import asyncio
import csv
import io
import threading

from django.contrib.auth import get_user_model
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Auction, AuctionTeam, Bid, Player, SoldResult, Team
from .permissions import IsManagerPermission, IsCaptainPermission
from .serializers import (
    AuctionCreateSerializer,
    AuctionDetailSerializer,
    AuctionListSerializer,
    AuctionTeamSerializer,
    BidSerializer,
    PlayerSerializer,
    SoldResultSerializer,
    TeamCreateSerializer,
    TeamSerializer,
)

User = get_user_model()


# ── Helpers ──────────────────────────────────────────────────────────────────

# Registry to track active timer tasks per auction — prevents multiple loops
_active_timers = {}  # auction_id -> threading.Event (stop signal)
_active_timers_lock = threading.Lock()


def get_channel_layer_broadcast(group_name, message):
    """Sync helper to broadcast to a channel group."""
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(group_name, message)


def _cancel_timer(auction_id):
    """Signal any existing timer loop for this auction to stop."""
    with _active_timers_lock:
        event = _active_timers.get(auction_id)
        if event:
            event.set()


# Registry to track active countdown tasks per auction
_active_countdowns = {}  # auction_id -> threading.Event
_active_countdowns_lock = threading.Lock()


def _cancel_countdown(auction_id):
    """Signal any in-progress 3-2-1 countdown to abort."""
    with _active_countdowns_lock:
        event = _active_countdowns.get(auction_id)
        if event:
            event.set()


def _start_timer(auction_id):
    """Module-level helper: cancel old timer and start a fresh one from DB value."""
    _cancel_timer(auction_id)
    stop_event = threading.Event()
    with _active_timers_lock:
        _active_timers[auction_id] = stop_event

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_timer(auction_id, stop_event))
        loop.close()
        with _active_timers_lock:
            if _active_timers.get(auction_id) is stop_event:
                del _active_timers[auction_id]

    threading.Thread(target=run, daemon=True).start()


def _start_countdown(auction_id):
    """Module-level helper: cancel old countdown and start a fresh one."""
    _cancel_countdown(auction_id)
    stop_event = threading.Event()
    with _active_countdowns_lock:
        _active_countdowns[auction_id] = stop_event

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_countdown(auction_id, stop_event))
        loop.close()
        with _active_countdowns_lock:
            if _active_countdowns.get(auction_id) is stop_event:
                del _active_countdowns[auction_id]

    threading.Thread(target=run, daemon=True).start()


# ── Auth Views ───────────────────────────────────────────────────────────────

class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()

        if not username or not password:
            return Response(
                {'error': 'Username and password required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response(
                {'error': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not user.check_password(password):
            return Response(
                {'error': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not user.is_active:
            return Response(
                {'error': 'Account disabled.'},
                status=status.HTTP_403_FORBIDDEN
            )

        refresh = RefreshToken.for_user(user)

        team_id = None
        team_name = None
        if user.role == 'captain':
            # Find team by captain_username field set when manager created the team
            captain_team = Team.objects.filter(captain_username=user.username).first()
            if captain_team:
                team_id = captain_team.id
                team_name = captain_team.name

        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': user.id,
                'username': user.username,
                'name': team_name or user.username,
                'role': user.role,
                'team_id': team_id,
                'team_name': team_name,
            }
        })


# ── Team Views ───────────────────────────────────────────────────────────────

class TeamListCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def get(self, request):
        teams = Team.objects.filter(created_by=request.user)
        serializer = TeamSerializer(teams, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = TeamCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Create team
        team = Team.objects.create(name=data['name'], created_by=request.user)

        # Create captain user
        captain = User.objects.create_user(
            username=data['username'],
            password=data['password'],
            role='captain',
        )
        # Link captain → team via a CaptainProfile or direct FK
        # Use a simple approach: store team on custom user model
        captain.save()

        # Store back-reference on team (for lookup)
        team.captain_username = data['username']
        team.save()

        return Response(
            {'id': team.id, 'name': team.name, 'captain_username': data['username']},
            status=status.HTTP_201_CREATED
        )


class TeamDetailView(APIView):
    permission_classes = [IsManagerPermission]

    def delete(self, request, pk):
        try:
            team = Team.objects.get(pk=pk, created_by=request.user)
        except Team.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Delete associated captain user
        captain = User.objects.filter(
            role='captain',
            username=getattr(team, 'captain_username', None)
        ).first()
        if captain:
            captain.delete()

        team.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Auction Views ─────────────────────────────────────────────────────────────

class AuctionListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsManagerPermission()]
        return [AllowAny()]

    def get(self, request):
        user = request.user
        if user.is_authenticated and user.role == 'manager':
            auctions = Auction.objects.filter(manager=user)
        elif user.is_authenticated and user.role == 'captain':
            # Show auctions this captain's team is assigned to
            team = Team.objects.filter(captain_username=user.username).first()
            if team:
                auction_ids = AuctionTeam.objects.filter(
                    team=team
                ).values_list('auction_id', flat=True)
                auctions = Auction.objects.filter(id__in=auction_ids)
            else:
                auctions = Auction.objects.none()
        else:
            # Public: show all active/pending/paused auctions
            auctions = Auction.objects.exclude(status='ended')

        serializer = AuctionListSerializer(auctions, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = AuctionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        team_ids = data.pop('team_ids')

        teams = Team.objects.filter(id__in=team_ids, created_by=request.user)
        if teams.count() != len(team_ids):
            return Response(
                {'error': 'One or more team IDs are invalid.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        auction = Auction.objects.create(manager=request.user, **data)

        # Add teams with starting balance
        for team in teams:
            AuctionTeam.objects.create(
                auction=auction,
                team=team,
                balance=auction.base_balance
            )

        return Response(
            AuctionListSerializer(auction).data,
            status=status.HTTP_201_CREATED
        )


class AuctionDetailView(APIView):
    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsManagerPermission()]
        return [AllowAny()]

    def get(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = AuctionDetailSerializer(auction)
        return Response(serializer.data)

    def delete(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id, manager=request.user)
            auction.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


# ── Player Views ──────────────────────────────────────────────────────────────

class PlayerListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        players = Player.objects.filter(auction=auction)
        serializer = PlayerSerializer(players, many=True)
        return Response(serializer.data)


class PlayerImportView(APIView):
    permission_classes = [IsManagerPermission]

    def post(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id, manager=request.user)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        file = request.FILES.get('file')
        if not file:
            return Response(
                {'error': 'No file provided.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            decoded = file.read().decode('utf-8-sig')  # handles BOM
            reader = csv.DictReader(io.StringIO(decoded))
        except Exception as e:
            return Response(
                {'error': f'Could not parse file: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        valid_levels = {'bigtime', 'epic', 'highlight', 'base'}
        players_created = []

        # Clear existing players if re-importing
        Player.objects.filter(auction=auction, sold=False).delete()

        def safe_int(v, default=0):
            try:
                msg = str(v).strip()
                if not msg: return default
                return int(float(msg))
            except (ValueError, TypeError):
                return default

        for i, row in enumerate(reader):
            try:
                # Handle potential case mismatches by normalizing keys
                norm_row = {k.strip().lower(): v for k, v in row.items() if k}
                
                name = norm_row.get('player_name', norm_row.get('name', '')).strip()
                if not name:
                    continue

                level = norm_row.get('level', 'base').strip().lower()
                if level not in valid_levels:
                    level = 'base'

                player = Player.objects.create(
                    auction=auction,
                    name=name,
                    base_price=safe_int(norm_row.get('base_price', norm_row.get('baseprice', 50)), 50),
                    level=level,
                    wins=safe_int(norm_row.get('wins', norm_row.get('win', 0)), 0),
                    losses=safe_int(norm_row.get('losses', norm_row.get('lose', 0)), 0),
                    goals=safe_int(norm_row.get('goals', norm_row.get('goal', 0)), 0),
                    order=i,
                )
                players_created.append(player)
            except Exception as e:
                print(f"Skipped row {i} due to error: {e}")
                continue  # Skip malformed rows

        return Response(
            {
                'imported': len(players_created),
                'players': PlayerSerializer(players_created, many=True).data,
            },
            status=status.HTTP_201_CREATED
        )


# ── Auction Control View ──────────────────────────────────────────────────────

class AuctionControlView(APIView):
    permission_classes = [IsManagerPermission]

    def post(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id, manager=request.user)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action')
        group_name = f'auction_{auction_id}'

        if action == 'start':
            # Cancel any old timer
            _cancel_timer(auction_id)
            auction.status = 'active'
            auction.current_timer = auction.time_limit
            if not auction.started_at:
                auction.started_at = timezone.now()
            auction.save(update_fields=['status', 'current_timer', 'started_at'])

            # Broadcast status change
            get_channel_layer_broadcast(group_name, {
                'type': 'auction_status',
                'status': 'active'
            })
            get_channel_layer_broadcast(group_name, {
                'type': 'timer_update',
                'timeLeft': auction.time_limit,
            })

            # Start server-side timer in background
            self._start_timer_task(auction_id)

        elif action == 'pause':
            auction.status = 'paused'
            auction.save(update_fields=['status'])
            get_channel_layer_broadcast(group_name, {
                'type': 'auction_status',
                'status': 'paused'
            })

        elif action == 'resume':
            # Cancel any lingering old timer
            _cancel_timer(auction_id)
            auction.status = 'active'
            auction.save(update_fields=['status'])
            get_channel_layer_broadcast(group_name, {
                'type': 'auction_status',
                'status': 'active'
            })
            self._start_timer_task(auction_id)

        elif action == 'next_player':
            result = self._advance_to_next_player(auction)
            if result.get('error'):
                return Response({'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)

            auction.refresh_from_db()
            player_data = PlayerSerializer(auction.current_player).data
            # Cancel any running timer for this auction
            _cancel_timer(auction_id)
            get_channel_layer_broadcast(group_name, {
                'type': 'player_update',
                'player': player_data,
                'timer': auction.current_timer,
            })
            # Reset timer display for all clients
            get_channel_layer_broadcast(group_name, {
                'type': 'timer_update',
                'timeLeft': auction.current_timer,
            })
            # Clear any lingering countdown overlay
            get_channel_layer_broadcast(group_name, {
                'type': 'bid_countdown',
                'value': None,
            })
            get_channel_layer_broadcast(group_name, {
                'type': 'auction_status',
                'status': 'pending'
            })

        elif action == 'start_count':
            if auction.status != 'active':
                return Response({'error': 'Auction must be active to start countdown.'}, status=400)

            # Cancel the running timer — countdown runs instead
            # Status stays 'active' so captains CAN still bid during 3-2-1
            _cancel_timer(auction_id)
            _start_countdown(auction_id)

        elif action == 'end_auction':
            auction.status = 'ended'
            auction.ended_at = timezone.now()
            auction.save(update_fields=['status', 'ended_at'])

            results = SoldResult.objects.filter(auction=auction).select_related('player', 'team')
            results_data = SoldResultSerializer(results, many=True).data

            get_channel_layer_broadcast(group_name, {
                'type': 'auction_end',
                'results': results_data
            })
        else:
            return Response(
                {'error': f'Unknown action: {action}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        auction.refresh_from_db()
        return Response({
            'status': auction.status,
            'action_performed': action,
            'current_player_id': auction.current_player_id,
            'timer': auction.current_timer,
        })

    def _advance_to_next_player(self, auction):
        """Find next unsold player, pushing current unsold player to the back of the queue."""
        current = auction.current_player
        
        if current:
            # Push current player to the end of the line
            current.skipped = True
            max_order = Player.objects.filter(
                auction=auction
            ).order_by('-order').values_list('order', flat=True).first() or 0
            current.order = max_order + 1
            current.save(update_fields=['skipped', 'order'])

        players = list(Player.objects.filter(auction=auction, sold=False, skipped=False).order_by('order'))

        if not players:
            # Try skipped players (which have been pushed to end)
            players = list(Player.objects.filter(auction=auction, sold=False).order_by('order'))
            if not players:
                return {'error': 'No more players in queue.'}

        next_player = players[0]

        auction.current_player = next_player
        auction.current_timer = auction.time_limit
        auction.status = 'pending'
        auction.save(update_fields=['current_player', 'current_timer', 'status'])
        return {'player': next_player}

    def _start_timer_task(self, auction_id):
        """Thin wrapper — delegates to module-level _start_timer."""
        _start_timer(auction_id)

    def _start_countdown_task(self, auction_id):
        """Thin wrapper — delegates to module-level _start_countdown."""
        _start_countdown(auction_id)


async def _run_timer(auction_id, stop_event):
    """Server-side countdown timer. Reads current_timer from DB each second
    so that bid-time-extensions and manual overrides are always respected.
    stop_event is a threading.Event; when set, the loop exits cleanly.
    """
    from .models import Auction
    from channels.layers import get_channel_layer
    from asgiref.sync import sync_to_async

    channel_layer = get_channel_layer()
    group_name = f'auction_{auction_id}'

    @sync_to_async
    def tick():
        """Decrement DB timer by 1, return (status, new_time_left)."""
        try:
            a = Auction.objects.get(id=auction_id)
            if a.status != 'active':
                return a.status, a.current_timer
            new_time = max(0, a.current_timer - 1)
            a.current_timer = new_time
            a.save(update_fields=['current_timer'])
            return a.status, new_time
        except Auction.DoesNotExist:
            return 'ended', 0

    while True:
        # Respect external stop signal (pause, next_player, start_count)
        if stop_event.is_set():
            break

        current_status, time_left = await tick()

        if current_status != 'active':
            break

        await channel_layer.group_send(group_name, {
            'type': 'timer_update',
            'timeLeft': time_left
        })

        if time_left == 0:
            await _handle_timer_expired(auction_id, channel_layer, group_name)
            break

        # Sleep 1 second but wake early if stopped
        for _ in range(10):
            if stop_event.is_set():
                break
            await asyncio.sleep(0.1)


async def _handle_timer_expired(auction_id, channel_layer, group_name):
    """When timer runs out: if no bids → UNSOLD; if bids → stop, wait for manager."""
    from .models import Auction, Bid, Player
    from asgiref.sync import sync_to_async

    @sync_to_async
    def check_and_mark_unsold():
        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return None

        if not auction.current_player:
            return None

        top_bid = Bid.objects.filter(
            auction=auction, player=auction.current_player
        ).order_by('-amount').first()

        if not top_bid:
            # No bids → mark UNSOLD, push to end of queue
            player = auction.current_player
            player.skipped = True
            player.save(update_fields=['skipped'])

            # Move to end by updating order
            max_order = Player.objects.filter(
                auction=auction
            ).order_by('-order').values_list('order', flat=True).first() or 0
            player.order = max_order + 1
            player.save(update_fields=['order'])

            auction.status = 'pending'
            auction.save(update_fields=['status'])
            return 'unsold'

        return 'has_bid'

    result = await check_and_mark_unsold()

    if result == 'unsold':
        await channel_layer.group_send(group_name, {
            'type': 'bid_countdown',
            'value': 'UNSOLD'
        })
        await channel_layer.group_send(group_name, {
            'type': 'auction_status',
            'status': 'pending'
        })


async def _run_countdown(auction_id, stop_event):
    """3-2-1 countdown then mark player SOLD and auto-advance.
    stop_event: if set mid-countdown (captain bid), abort and restart timer.
    """
    from .models import Auction, Bid, AuctionTeam, SoldResult, Player
    from .serializers import AuctionTeamSerializer, PlayerSerializer
    from asgiref.sync import sync_to_async
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    group_name = f'auction_{auction_id}'

    # Broadcast Call 1 → Call 2 → Call 3 with 2s gaps, abort early if a bid arrives
    for count in ["Call 1", "Call 2", "Call 3"]:
        if stop_event.is_set():
            # A bid was placed — clear overlay and restart timer
            await channel_layer.group_send(group_name, {
                'type': 'bid_countdown', 'value': None
            })
            _start_timer(auction_id)
            return

        await channel_layer.group_send(group_name, {
            'type': 'bid_countdown',
            'value': count
        })

        # Sleep 2 seconds in small chunks so we react to bids quickly
        for _ in range(20):
            if stop_event.is_set():
                await channel_layer.group_send(group_name, {
                    'type': 'bid_countdown', 'value': None
                })
                _start_timer(auction_id)
                return
            await asyncio.sleep(0.1)

    # Final check — did a bid arrive just before we mark SOLD?
    if stop_event.is_set():
        await channel_layer.group_send(group_name, {
            'type': 'bid_countdown', 'value': None
        })
        _start_timer(auction_id)
        return

    @sync_to_async
    def mark_sold_and_advance():
        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return None, None, None, 0

        if not auction.current_player:
            return None, None, None, 0

        top_bid = Bid.objects.filter(
            auction=auction, player=auction.current_player
        ).order_by('-amount').first()

        if not top_bid:
            # No bid — mark UNSOLD and advance to back of queue
            player = auction.current_player
            player.skipped = True
            max_order = Player.objects.filter(
                auction=auction
            ).order_by('-order').values_list('order', flat=True).first() or 0
            player.order = max_order + 1
            player.save(update_fields=['skipped', 'order'])
            sold_msg = 'UNSOLD'
        else:
            # Record the sale
            SoldResult.objects.create(
                auction=auction,
                player=auction.current_player,
                team=top_bid.team,
                sold_price=top_bid.amount,
            )
            auction.current_player.sold = True
            auction.current_player.save(update_fields=['sold'])

            # Deduct balance
            try:
                at = AuctionTeam.objects.get(auction=auction, team=top_bid.team)
                at.balance = max(0, at.balance - top_bid.amount)
                at.save(update_fields=['balance'])
            except AuctionTeam.DoesNotExist:
                pass

            sold_msg = f'SOLD TO\n{top_bid.team.name}!'

        # Updated teams
        auction_teams = AuctionTeam.objects.filter(auction=auction).select_related('team')
        teams_data = AuctionTeamSerializer(auction_teams, many=True).data

        # ── Auto-advance to next player ──────────────────────────────────
        players = list(Player.objects.filter(
            auction=auction, sold=False, skipped=False
        ).order_by('order'))

        if not players:
            # Fall back to skipped players
            players = list(Player.objects.filter(
                auction=auction, sold=False
            ).order_by('order'))

        current = auction.current_player
        next_player = None

        if players:
            next_player = players[0]

        if next_player:
            auction.current_player = next_player
            auction.current_timer = auction.time_limit
            auction.status = 'pending'
            auction.save(update_fields=['current_player', 'current_timer', 'status'])
            next_player_data = PlayerSerializer(next_player).data
        else:
            # No more players
            auction.status = 'ended'
            auction.save(update_fields=['status'])
            next_player_data = None

        return sold_msg, teams_data, next_player_data, auction.time_limit

    sold_msg, teams_data, next_player_data, time_limit = await mark_sold_and_advance()

    if not sold_msg:
        return

    # Show SOLD / UNSOLD overlay for 2.5s
    await channel_layer.group_send(group_name, {
        'type': 'bid_countdown', 'value': sold_msg
    })
    await asyncio.sleep(2.5)
    await channel_layer.group_send(group_name, {
        'type': 'bid_countdown', 'value': None  # Clear overlay
    })

    if teams_data:
        await channel_layer.group_send(group_name, {
            'type': 'teams_update', 'teams': teams_data
        })

    if next_player_data:
        # Auto-advance: push new player + reset timer for all clients
        await channel_layer.group_send(group_name, {
            'type': 'player_update',
            'player': next_player_data,
            'timer': time_limit,
        })
        await channel_layer.group_send(group_name, {
            'type': 'timer_update', 'timeLeft': time_limit
        })
        await channel_layer.group_send(group_name, {
            'type': 'auction_status', 'status': 'pending'
        })
    else:
        # Auction over
        await channel_layer.group_send(group_name, {
            'type': 'auction_status', 'status': 'ended'
        })


# ── Bid Views ─────────────────────────────────────────────────────────────────

class BidListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        bids = Bid.objects.filter(auction=auction).select_related('team')[:50]
        return Response(BidSerializer(bids, many=True).data)

    def post(self, request, auction_id):
        if request.user.role != 'captain':
            return Response(
                {'error': 'Only captains can place bids.'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if auction.status != 'active':
            return Response(
                {'error': 'Auction is not active.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not auction.current_player:
            return Response(
                {'error': 'No player on auction block.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        amount = request.data.get('amount')
        if not amount:
            return Response(
                {'error': 'Bid amount required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            amount = int(amount)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid amount.'}, status=400)

        # Get team
        team = Team.objects.filter(captain_username=request.user.username).first()
        if not team:
            return Response({'error': 'No team found for this captain.'}, status=400)

        try:
            auction_team = AuctionTeam.objects.get(auction=auction, team=team)
        except AuctionTeam.DoesNotExist:
            return Response({'error': 'Your team is not in this auction.'}, status=403)

        top_bid = Bid.objects.filter(
            auction=auction, player=auction.current_player
        ).order_by('-amount').first()

        floor = top_bid.amount if top_bid else auction.current_player.base_price

        if amount <= floor:
            return Response(
                {'error': f'Bid must exceed ${floor}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount > auction_team.balance:
            return Response(
                {'error': f'Not enough balance. You have ${auction_team.balance}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check max roster limit
        won_count = SoldResult.objects.filter(auction=auction, team=team).count()
        if won_count >= auction.max_players_per_team:
            return Response(
                {'error': f'Roster full. You already have {won_count}/{auction.max_players_per_team} players.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        bid = Bid.objects.create(
            auction=auction,
            player=auction.current_player,
            team=team,
            amount=amount,
        )

        # Extend timer if < 10s
        timer_extended = False
        if auction.current_timer < 10:
            auction.current_timer = 10
            auction.save(update_fields=['current_timer'])
            timer_extended = True

        bid_data = {
            'id': bid.id,
            'team_id': team.id,
            'team_name': team.name,
            'amount': bid.amount,
            'timestamp': bid.timestamp.isoformat(),
        }

        # Broadcast bid via WebSocket
        group_name = f'auction_{auction_id}'
        get_channel_layer_broadcast(group_name, {
            'type': 'bid_update',
            'bid': bid_data
        })
        if timer_extended:
            get_channel_layer_broadcast(group_name, {
                'type': 'timer_update',
                'timeLeft': 10
            })

        # If a 3-2-1 countdown was in progress, this bid cancels it
        if _active_countdowns.get(auction_id):
            _cancel_countdown(auction_id)
            # Clear the countdown overlay for all clients
            get_channel_layer_broadcast(group_name, {
                'type': 'bid_countdown', 'value': None
            })
            # Restart the timer so bidding continues
            _start_timer(auction_id)

        return Response(bid_data, status=status.HTTP_201_CREATED)


# ── Results View ──────────────────────────────────────────────────────────────

class ResultListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        results = SoldResult.objects.filter(auction=auction).select_related('player', 'team')
        return Response(SoldResultSerializer(results, many=True).data)
