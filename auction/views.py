import asyncio
import csv
import io
import random
import string
import threading

import openpyxl

from django.contrib.auth import get_user_model
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .card_links import CardLinkError, normalise_card_link
from .models import (
    Auction,
    AuctionTeam,
    Bid,
    FixtureCompetition,
    FixtureGroup,
    FixtureLineup,
    FixtureMatch,
    FixtureRosterEntry,
    FixtureSeason,
    Player,
    SoldResult,
    Team,
    CustomTournament,
)
from .permissions import IsManagerPermission, IsCaptainPermission
from .stats import _fixture_table, _fixture_group_tables, _fixture_player_stats
from .serializers import (
    AuctionCreateSerializer,
    AuctionDetailSerializer,
    AuctionListSerializer,
    AuctionTeamSerializer,
    BidSerializer,
    FixtureCompetitionCreateSerializer,
    FixtureCompetitionDetailSerializer,
    FixtureCompetitionListSerializer,
    FixtureCompetitionQuickCreateSerializer,
    FixtureCompetitionStatusUpdateSerializer,
    FixtureGroupSerializer,
    FixtureMatchSerializer,
    FixtureRosterEntrySerializer,
    FixtureSeasonSerializer,
    PlayerSerializer,
    SoldResultSerializer,
    TeamCreateSerializer,
    TeamRegistrySerializer,
    TeamSerializer,
    UserSerializer,
    CustomTournamentSerializer,
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


def price_locked_max_bid(auction, auction_team, won_count):
    """Price Value Lock: the highest a team may bid on the current player while still
    being able to afford its remaining required roster slots at (at least) the
    cheapest still-unsold base price. Returns None when the lock doesn't apply
    (feature off, or this can be the team's last required player).
    """
    if not auction.price_lock_enabled:
        return None

    # Per-team override when set, otherwise the auction-wide limit.
    slots_after_this = auction_team.roster_limit - won_count - 1
    if slots_after_this <= 0:
        return None

    from .models import Player
    min_base_price = Player.objects.filter(
        auction=auction, sold=False
    ).exclude(id=auction.current_player_id).order_by('base_price').values_list('base_price', flat=True).first() or 0

    reserve = slots_after_this * min_base_price
    return auction_team.balance - reserve


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
        serializer = TeamSerializer(teams, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request):
        serializer = TeamCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Create team
        team = Team.objects.create(
            name=data['name'],
            logo=data.get('logo'),
            primary_color=data.get('primary_color', '#1F3322'),
            created_by=request.user
        )

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


class TeamRegistryView(APIView):
    """Every team with the competitions and auctions it appears in."""

    permission_classes = [IsManagerPermission]

    def get(self, request):
        teams = (
            Team.objects.filter(created_by=request.user)
            .prefetch_related('fixture_competitions', 'auction_set')
            .order_by('name')
        )
        return Response(
            TeamRegistrySerializer(teams, many=True, context={'request': request}).data
        )


class TeamDetailView(APIView):
    permission_classes = [IsManagerPermission]

    def patch(self, request, pk):
        try:
            team = Team.objects.get(pk=pk, created_by=request.user)
        except Team.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = TeamSerializer(team, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
            captain.is_active = False
            captain.save(update_fields=['is_active'])

        team.soft_delete()
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

        overrides = {
            int(o['team_id']): o
            for o in data.pop('team_overrides', []) or []
            if o.get('team_id') is not None
        }
        data.pop('team_ids', None)

        auction = Auction.objects.create(manager=request.user, **data)

        # Add teams, honouring any per-team budget / squad-size override.
        def positive_int(value):
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None
            return number if number > 0 else None

        for team in teams:
            override = overrides.get(team.id, {})
            AuctionTeam.objects.create(
                auction=auction,
                team=team,
                balance=positive_int(override.get('balance')) or auction.base_balance,
                max_players=positive_int(override.get('max_players')),
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
            auction.soft_delete()
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

        # .xlsx is a binary zip, so it has to be read with openpyxl rather than
        # decoded as text the way a CSV is.
        raw = file.read()
        if (file.name or '').lower().endswith(('.xlsx', '.xlsm')):
            try:
                workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
                sheet = workbook.active
                rows = sheet.iter_rows(values_only=True)
                headers = [str(h).strip() if h is not None else '' for h in next(rows)]
                reader = [
                    dict(zip(headers, row))
                    for row in rows
                    if any(cell is not None and str(cell).strip() for cell in row)
                ]
            except StopIteration:
                return Response(
                    {'error': 'The spreadsheet is empty.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                return Response(
                    {'error': f'Could not read the spreadsheet: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            try:
                decoded = raw.decode('utf-8-sig')  # handles BOM
                reader = csv.DictReader(io.StringIO(decoded))
            except Exception as e:
                return Response(
                    {'error': f'Could not parse file: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        valid_levels = {'bigtime', 'epic', 'showtime', 'highlight', 'base'}
        players_created = []
        card_link_warnings = []

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

                def cell(*keys, default=''):
                    """First non-empty value among these column names, as text."""
                    for key in keys:
                        value = norm_row.get(key)
                        if value is not None and str(value).strip():
                            return str(value).strip()
                    return default

                name = cell('player_name', 'name')
                if not name:
                    continue

                level = cell('level', default='base').lower()
                if level not in valid_levels:
                    level = 'base'

                # The sheet has gone out with both header spellings; accept either.
                raw_link = cell('participant_card_link', 'player_card_link', 'card_link')
                try:
                    card_image_url = normalise_card_link(raw_link)
                except CardLinkError as e:
                    card_image_url = ''
                    card_link_warnings.append(f'{name}: {e}')

                player = Player.objects.create(
                    auction=auction,
                    name=name,
                    position=cell('position')[:10],
                    card_image_url=card_image_url,
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
                # Surfaced in the dashboard so a bad card link is caught at import
                # time rather than as a blank card mid-auction.
                'card_link_warnings': card_link_warnings,
            },
            status=status.HTTP_201_CREATED
        )


class PlayerPriceUpdateView(APIView):
    """Lets a manager manually adjust an unsold player's base price mid-auction —
    e.g. to break a deadlock where remaining teams' balances have fallen below
    what Price Value Lock requires them to reserve, faster than waiting on the
    automatic per-skip price_decrement.
    """
    permission_classes = [IsManagerPermission]

    def patch(self, request, auction_id, player_id):
        try:
            auction = Auction.objects.get(id=auction_id, manager=request.user)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            player = Player.objects.get(id=player_id, auction=auction)
        except Player.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if player.sold:
            return Response(
                {'error': 'Cannot change the price of a player that has already been sold.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            base_price = int(request.data.get('base_price'))
        except (TypeError, ValueError):
            return Response({'error': 'base_price must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        if base_price < 0:
            return Response({'error': 'base_price cannot be negative.'}, status=status.HTTP_400_BAD_REQUEST)

        player.base_price = base_price
        player.save(update_fields=['base_price'])

        group_name = f'auction_{auction_id}'
        players_data = PlayerSerializer(Player.objects.filter(auction=auction), many=True).data
        get_channel_layer_broadcast(group_name, {
            'type': 'roster_update',
            'players': players_data,
        })
        if auction.current_player_id == player.id:
            get_channel_layer_broadcast(group_name, {
                'type': 'player_update',
                'player': PlayerSerializer(player).data,
                'timer': auction.current_timer,
            })

        return Response(PlayerSerializer(player).data)


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

            # Broadcast final teams state BEFORE auction_end so completion screen
            # always renders with the latest player rosters
            final_teams = AuctionTeam.objects.filter(auction=auction).select_related('team')
            final_teams_data = AuctionTeamSerializer(final_teams, many=True).data
            get_channel_layer_broadcast(group_name, {
                'type': 'teams_update',
                'teams': final_teams_data,
            })

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
            current.base_price = max(0, current.base_price - auction.price_decrement)
            max_order = Player.objects.filter(
                auction=auction
            ).order_by('-order').values_list('order', flat=True).first() or 0
            current.order = max_order + 1
            current.save(update_fields=['skipped', 'order', 'base_price'])

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
            player.base_price = max(0, player.base_price - auction.price_decrement)

            # Move to end by updating order
            max_order = Player.objects.filter(
                auction=auction
            ).order_by('-order').values_list('order', flat=True).first() or 0
            player.order = max_order + 1
            player.save(update_fields=['skipped', 'order', 'base_price'])

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
            player.base_price = max(0, player.base_price - auction.price_decrement)
            max_order = Player.objects.filter(
                auction=auction
            ).order_by('-order').values_list('order', flat=True).first() or 0
            player.order = max_order + 1
            player.save(update_fields=['skipped', 'order', 'base_price'])
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

        # The opening bid on a player may match the base price exactly (first team
        # to act claims it at that price); every bid after that must strictly raise it.
        if (amount < floor) if not top_bid else (amount <= floor):
            return Response(
                {'error': f'Bid must exceed ${floor}.' if top_bid else f'Bid must be at least ${floor}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount > auction_team.balance:
            return Response(
                {'error': f'Not enough balance. You have ${auction_team.balance}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check max roster limit
        won_count = SoldResult.objects.filter(auction=auction, team=team).count()
        roster_limit = auction_team.roster_limit
        if won_count >= roster_limit:
            return Response(
                {'error': f'Roster full. You already have {won_count}/{roster_limit} players.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Price Value Lock: keep enough balance in reserve to still afford the
        # rest of the required roster once this bid wins.
        max_locked_bid = price_locked_max_bid(auction, auction_team, won_count)
        if max_locked_bid is not None and amount > max_locked_bid:
            return Response(
                {'error': f'Price Lock: max bid is ${max_locked_bid} — you must reserve enough balance for your remaining roster slots.'},
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


# ── Fixture Views ─────────────────────────────────────────────────────────────

def _generate_league_matches(competition, teams, group=None):
    if not teams:
        return

    # Proper Round-Robin Scheduling (Circle Method)
    team_list = list(teams)
    if len(team_list) % 2 != 0:
        team_list.append(None)  # Add dummy team for 'bye'

    n = len(team_list)
    base_rounds = []
    
    # Generate one full round-robin set (N-1 rounds)
    temp_teams = list(team_list)
    for r in range(n - 1):
        round_matches = []
        for i in range(n // 2):
            home = temp_teams[i]
            away = temp_teams[n - 1 - i]
            if home and away:
                # Alternate home/away for balance
                if r % 2 == 0:
                    round_matches.append((home, away))
                else:
                    round_matches.append((away, home))
        base_rounds.append(round_matches)
        # Rotate: keep index 0, move last to second position
        temp_teams = [temp_teams[0]] + [temp_teams[-1]] + temp_teams[1:-1]

    # Handle multiple legs (e.g. Home and Away)
    all_legs_rounds = []
    for leg in range(competition.matches_per_pair):
        for r_idx, r_matches in enumerate(base_rounds):
            leg_round = []
            for home, away in r_matches:
                if leg % 2 == 0:
                    leg_round.append((home, away))
                else:
                    leg_round.append((away, home))
            all_legs_rounds.append(leg_round)

    # Map rounds to match days
    fixture_matches = []
    order = 0
    
    for round_idx, round_matches in enumerate(all_legs_rounds):
        # We use the configured match_days. 
        # Ideally, match_days >= len(all_legs_rounds) to avoid double matches.
        m_day = (round_idx % competition.match_days) + 1
        
        for home, away in round_matches:
            fixture_matches.append(FixtureMatch(
                competition=competition,
                group=group,
                home_team=home,
                away_team=away,
                stage='league',
                match_day=m_day,
                order=order,
            ))
            order += 1

    FixtureMatch.objects.bulk_create(fixture_matches)


def _seed_fixture_rosters(competition, teams):
    sold_results = SoldResult.objects.filter(
        auction=competition.auction,
        team__in=teams,
    ).select_related('team', 'player')
    for result in sold_results:
        FixtureRosterEntry.objects.get_or_create(
            competition=competition,
            team=result.team,
            name=result.player.name,
            defaults={
                'player': result.player,
                'is_custom': False,
                'is_active': True,
            }
        )


def _group_name(index):
    """0 -> 'Group A', 25 -> 'Group Z', 26 -> 'Group AA', ..."""
    letters = string.ascii_uppercase
    label = ''
    n = index
    while True:
        label = letters[n % 26] + label
        n = n // 26 - 1
        if n < 0:
            break
    return f'Group {label}'


def _split_teams_into_groups(teams, group_count):
    """Shuffle teams and split them as evenly as possible across group_count buckets."""
    shuffled = list(teams)
    random.shuffle(shuffled)
    buckets = [[] for _ in range(group_count)]
    for i, team in enumerate(shuffled):
        buckets[i % group_count].append(team)
    return buckets


class FixtureCreationError(Exception):
    """Raised by _create_fixture_competition on validation failure — callers
    turn it into an HTTP 400 with the message as the error."""
    pass


def _create_fixture_competition(auction, teams, data):
    """Shared by FixtureCompetitionListCreateView and FixtureQuickCreateView:
    creates the FixtureCompetition, seeds rosters, and generates whatever
    initial matches the format needs (league schedule, per-group schedules,
    or nothing yet for a standalone knockout bracket).
    """
    group_count = data.get('group_count')
    is_group_stage = data.get('format_type') == 'group_stage' and group_count
    is_pure_knockout = data.get('format_type') == 'knockout'

    if is_group_stage and group_count > len(teams) // 2:
        raise FixtureCreationError(
            'Not enough teams to form that many groups (each group needs at least 2 teams).'
        )

    competition = FixtureCompetition.objects.create(
        auction=auction,
        season_id=data.get('season'),
        title=data['title'],
        match_type=data['match_type'],
        format_type=data.get('format_type', 'ezone_custom'),
        matches_per_pair=data['matches_per_pair'],
        match_days=data['match_days'],
        semifinal_qualifiers=data['semifinal_qualifiers'],
        teams_per_group_advance=data['teams_per_group_advance'],
    )
    competition.teams.set(teams)
    _seed_fixture_rosters(competition, teams)

    if is_group_stage:
        buckets = _split_teams_into_groups(teams, group_count)
        for index, group_teams in enumerate(buckets):
            group = FixtureGroup.objects.create(
                competition=competition, name=_group_name(index), order=index
            )
            group.teams.set(group_teams)
            _generate_league_matches(competition, group_teams, group=group)
    elif not is_pure_knockout:
        _generate_league_matches(competition, teams)

    return competition


class FixtureCompetitionPagination(PageNumberPagination):
    page_size = 6


class AllFixtureCompetitionsView(generics.ListAPIView):
    """Every tournament across every auction owned by this manager, paginated
    6 at a time for the Tournament Center's scroll-to-load-more list — one
    query per page instead of fetching each auction's fixtures separately
    (quick-created tournaments each live in their own dedicated auction, so
    that used to mean one request per tournament)."""
    permission_classes = [IsManagerPermission]
    serializer_class = FixtureCompetitionListSerializer
    pagination_class = FixtureCompetitionPagination

    def get_queryset(self):
        # FixtureCompetition.objects already excludes soft-deleted competitions
        # (AliveManager is the default manager) — auction__is_deleted=False adds
        # the same guarantee for the parent auction, since a related-field filter
        # like auction__manager joins the raw table and bypasses that default.
        return FixtureCompetition.objects.filter(
            auction__manager=self.request.user,
            auction__is_deleted=False,
        ).select_related('auction', 'season').order_by('-created_at')


class FixtureCompetitionListCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def get(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id, manager=request.user)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        competitions = FixtureCompetition.objects.filter(auction=auction)
        return Response(FixtureCompetitionListSerializer(competitions, many=True).data)

    def post(self, request, auction_id):
        try:
            auction = Auction.objects.get(id=auction_id, manager=request.user)
        except Auction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = FixtureCompetitionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        teams = list(Team.objects.filter(
            id__in=data['team_ids'],
            auctionteam__auction=auction,
        ).distinct())
        if len(teams) != len(set(data['team_ids'])):
            return Response(
                {'error': 'One or more team IDs are not assigned to this auction.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            competition = _create_fixture_competition(auction, teams, data)
        except FixtureCreationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            FixtureCompetitionListSerializer(competition).data,
            status=status.HTTP_201_CREATED
        )


class FixtureQuickCreateView(APIView):
    """Creates a tournament without a pre-existing auction: teams are entered
    by name/captain right here. Under the hood this still creates a normal
    Auction + Teams + AuctionTeams (everything else — matches, rosters, stats —
    is keyed off auction_id), it's just flagged is_fixture_only=True so it
    stays out of the manager's auctions dashboard and no live bidding ever runs
    against it.
    """
    permission_classes = [IsManagerPermission]

    def post(self, request):
        serializer = FixtureCompetitionQuickCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        auction = Auction.objects.create(
            manager=request.user,
            title=data['title'],
            is_fixture_only=True,
        )

        teams = []
        for team_data in data['teams']:
            team = Team.objects.create(
                name=team_data['name'].strip(),
                captain_username=team_data.get('captain', '').strip(),
                created_by=request.user,
            )
            AuctionTeam.objects.create(auction=auction, team=team, balance=auction.base_balance)
            teams.append(team)

        try:
            competition = _create_fixture_competition(auction, teams, data)
        except FixtureCreationError as e:
            # Hard delete on purpose: rolls back an auction created seconds ago in
            # this same request, which no user has seen. Nothing to recover.
            auction.delete()
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            FixtureCompetitionListSerializer(competition).data,
            status=status.HTTP_201_CREATED
        )


class FixtureSeasonListCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def get(self, request):
        seasons = FixtureSeason.objects.all()
        return Response(FixtureSeasonSerializer(seasons, many=True).data)

    def post(self, request):
        name = request.data.get('name', '').strip()
        if not name:
            return Response({'error': 'Season name is required.'}, status=400)
        season, created = FixtureSeason.objects.get_or_create(
            name=name,
            defaults={'is_active': bool(request.data.get('is_active', True))}
        )
        return Response(
            FixtureSeasonSerializer(season).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class FixtureCompetitionDetailView(APIView):
    permission_classes = [IsManagerPermission]

    def get_object(self, request, auction_id, fixture_id):
        return FixtureCompetition.objects.get(
            id=fixture_id, auction_id=auction_id, auction__manager=request.user
        )

    def get(self, request, auction_id, fixture_id):
        try:
            competition = self.get_object(request, auction_id, fixture_id)
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        goal_stats, defence_stats, stats_meta = _fixture_player_stats(competition, request)
        serializer = FixtureCompetitionDetailSerializer(
            competition,
            context={
                'request': request,
                'table': _fixture_table(competition, request),
                'group_tables': _fixture_group_tables(competition, request),
                'goal_stats': goal_stats,
                'defence_stats': defence_stats,
                'defence_stats_meta': stats_meta,
            }
        )
        return Response(serializer.data)

    def patch(self, request, auction_id, fixture_id):
        try:
            competition = self.get_object(request, auction_id, fixture_id)
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
            
        update_fields = []
        if 'title' in request.data:
            title = request.data.get('title', '').strip()
            if not title:
                return Response({'error': 'Title cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
            competition.title = title
            update_fields.append('title')
            
        if 'format_type' in request.data:
            format_type = request.data.get('format_type')
            valid_formats = [c[0] for c in FixtureCompetition.FORMAT_TYPE_CHOICES]
            if format_type in valid_formats:
                competition.format_type = format_type
                update_fields.append('format_type')

        if update_fields:
            competition.save(update_fields=update_fields)

        return Response({
            'id': competition.id, 
            'title': competition.title,
            'format_type': competition.format_type
        })

    def delete(self, request, auction_id, fixture_id):
        try:
            competition = self.get_object(request, auction_id, fixture_id)
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Flag the children too, so a match or roster entry can't resurface on its
        # own if it is ever queried without going through the competition.
        competition.matches.update(is_deleted=True, deleted_at=timezone.now())
        competition.roster_entries.update(is_deleted=True, deleted_at=timezone.now())
        competition.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FixtureCompetitionStatusUpdateView(APIView):
    permission_classes = [IsManagerPermission]

    def patch(self, request, auction_id, fixture_id):
        try:
            competition = FixtureCompetition.objects.get(
                id=fixture_id, auction_id=auction_id, auction__manager=request.user
            )
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = FixtureCompetitionStatusUpdateSerializer(
            competition, data=request.data, partial=True
        )
        if serializer.is_valid():
            if serializer.validated_data.get('is_default'):
                FixtureCompetition.objects.exclude(id=competition.id).update(is_default=False)
            serializer.save()
            if competition.status == 'completed':
                from players.rating_engine import handle_competition_completed
                handle_competition_completed(competition)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FixtureMatchUpdateView(APIView):
    permission_classes = [IsManagerPermission]

    def patch(self, request, auction_id, fixture_id, match_id):
        try:
            match = FixtureMatch.objects.select_related('competition').get(
                id=match_id,
                competition_id=fixture_id,
                competition__auction_id=auction_id,
                competition__auction__manager=request.user,
            )
        except FixtureMatch.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        lineups = request.data.get('lineups')
        if isinstance(lineups, list):
            match.lineups.all().delete()
            home_total = 0
            away_total = 0
            for index, row in enumerate(lineups):
                home_roster_entry_id = row.get('home_roster_entry')
                away_roster_entry_id = row.get('away_roster_entry')
                home_entry = FixtureRosterEntry.objects.filter(
                    id=home_roster_entry_id,
                    competition=match.competition,
                    team=match.home_team,
                    is_active=True,
                ).first() if home_roster_entry_id else None
                away_entry = FixtureRosterEntry.objects.filter(
                    id=away_roster_entry_id,
                    competition=match.competition,
                    team=match.away_team,
                    is_active=True,
                ).first() if away_roster_entry_id else None
                home_player_id = home_entry.player_id if home_entry else row.get('home_player')
                away_player_id = away_entry.player_id if away_entry else row.get('away_player')
                home_goals = max(0, int(row.get('home_goals') or 0))
                away_goals = max(0, int(row.get('away_goals') or 0))

                # A shootout belongs to the individual player set, and only counts
                # when that set finished level and the shootout produced a winner.
                home_pen = max(0, int(row.get('home_penalty') or 0))
                away_pen = max(0, int(row.get('away_penalty') or 0))
                shootout = bool(row.get('penalty_shootout'))
                if home_goals != away_goals or home_pen == away_pen:
                    shootout, home_pen, away_pen = False, 0, 0

                FixtureLineup.objects.create(
                    match=match,
                    home_roster_entry=home_entry,
                    away_roster_entry=away_entry,
                    home_player_id=home_player_id or None,
                    away_player_id=away_player_id or None,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    penalty_shootout=shootout,
                    home_penalty=home_pen,
                    away_penalty=away_pen,
                    order=index,
                )
                home_total += home_goals
                away_total += away_goals

            if match.competition.match_type == 'team':
                # Sets won — a set decided on penalties counts for its shootout winner.
                sides = [row.winner_side for row in match.lineups.all()]
                match.home_score = sides.count('home')
                match.away_score = sides.count('away')
            else:
                match.home_score = home_total
                match.away_score = away_total

        if 'home_score' in request.data:
            match.home_score = max(0, int(request.data.get('home_score') or 0))
        if 'away_score' in request.data:
            match.away_score = max(0, int(request.data.get('away_score') or 0))

        if request.data.get('status') in {'upcoming', 'completed'}:
            match.status = request.data['status']
        if match.status == 'completed' and not match.played_at:
            match.played_at = timezone.now()
        if match.status == 'upcoming':
            match.played_at = None

        match.save(update_fields=[
            'home_score', 'away_score', 'status', 'played_at'
        ])
        if match.competition.status == 'completed':
            from players.rating_engine import handle_competition_completed
            handle_competition_completed(match.competition)
        else:
            # Ongoing tournaments: keep player ratings in sync with results as they
            # come in, without waiting for the whole competition to be marked complete.
            from players.rating_engine import recompute_competition_profiles
            recompute_competition_profiles(match.competition)
        return Response(FixtureMatchSerializer(match).data)

    def delete(self, request, auction_id, fixture_id, match_id):
        try:
            match = FixtureMatch.objects.select_related('competition').get(
                id=match_id,
                competition_id=fixture_id,
                competition__auction_id=auction_id,
                competition__auction__manager=request.user,
            )
        except FixtureMatch.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        match.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

KNOCKOUT_STAGES = {'round_of_32', 'round_of_16', 'quarter', 'semi', 'final'}
STAGES_ORDER = ['league', 'round_of_32', 'round_of_16', 'quarter', 'semi', 'final']


class FixtureStageDeleteView(APIView):
    """Delete all matches belonging to a specific knockout stage."""
    permission_classes = [IsManagerPermission]

    def delete(self, request, auction_id, fixture_id, stage):
        if stage not in KNOCKOUT_STAGES:
            return Response({'error': f'stage must be one of {sorted(KNOCKOUT_STAGES)}.'}, status=400)
        try:
            competition = FixtureCompetition.objects.get(
                id=fixture_id,
                auction_id=auction_id,
                auction__manager=request.user,
            )
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        count = FixtureMatch.objects.filter(competition=competition, stage=stage).update(
            is_deleted=True, deleted_at=timezone.now()
        )
        return Response({'deleted': count})


class FixtureKnockoutCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def post(self, request, auction_id, fixture_id):
        try:
            competition = FixtureCompetition.objects.get(
                id=fixture_id,
                auction_id=auction_id,
                auction__manager=request.user,
            )
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        stage = request.data.get('stage')
        if stage not in KNOCKOUT_STAGES:
            return Response({'error': f'stage must be one of {sorted(KNOCKOUT_STAGES)}.'}, status=400)

        matches_per_pair = max(1, int(request.data.get('matches_per_pair') or 1))

        # Support custom pairs [[home_id, away_id], ...] OR legacy flat team_ids
        pairs_raw = request.data.get('pairs')
        if pairs_raw:
            if not isinstance(pairs_raw, list) or len(pairs_raw) == 0:
                return Response({'error': 'pairs must be a non-empty list of [home_id, away_id].'}, status=400)
            for pair in pairs_raw:
                if not isinstance(pair, list) or len(pair) != 2:
                    return Response({'error': 'Each pair must be [home_id, away_id].'}, status=400)
            all_ids = [tid for pair in pairs_raw for tid in pair]
            teams_qs = list(Team.objects.filter(id__in=all_ids, fixture_competitions=competition))
            team_by_id = {t.id: t for t in teams_qs}
            missing = [tid for tid in all_ids if tid not in team_by_id]
            if missing:
                return Response({'error': 'One or more teams are not in this fixture.'}, status=400)
            pairs = [[team_by_id[p[0]], team_by_id[p[1]]] for p in pairs_raw]
        else:
            team_ids = request.data.get('team_ids') or []
            if len(team_ids) < 2 or len(team_ids) % 2 != 0:
                return Response(
                    {'error': 'Provide an even number of teams in team_ids.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            teams_qs = list(Team.objects.filter(id__in=team_ids, fixture_competitions=competition))
            team_by_id = {t.id: t for t in teams_qs}
            missing = [tid for tid in team_ids if tid not in team_by_id]
            if missing:
                return Response({'error': 'One or more teams are not in this fixture.'}, status=400)
            pairs = [
                [team_by_id[team_ids[i]], team_by_id[team_ids[i + 1]]]
                for i in range(0, len(team_ids), 2)
            ]

        # Regenerating a stage wipes any results already entered against it, so the
        # old matches are flagged rather than dropped and stay restorable in admin.
        FixtureMatch.objects.filter(competition=competition, stage=stage).update(
            is_deleted=True, deleted_at=timezone.now()
        )

        # Calculate starting match day based on previous stages
        current_index = STAGES_ORDER.index(stage)
        prev_stages = STAGES_ORDER[:current_index]
        
        last_match = FixtureMatch.objects.filter(
            competition=competition, 
            stage__in=prev_stages
        ).order_by('-match_day').first()
        
        match_day_start = (last_match.match_day if last_match else competition.match_days) + 1

        created = []
        order = 0
        for home_team, away_team in pairs:
            for leg in range(matches_per_pair):
                h, a = (home_team, away_team) if leg % 2 == 0 else (away_team, home_team)
                created.append(FixtureMatch.objects.create(
                    competition=competition,
                    home_team=h,
                    away_team=a,
                    stage=stage,
                    match_day=match_day_start + leg,
                    order=order,
                ))
                order += 1

        return Response(FixtureMatchSerializer(created, many=True).data, status=201)


class FixtureGroupReassignView(APIView):
    """Move a team into a different group, regenerating that group's league matches."""
    permission_classes = [IsManagerPermission]

    def patch(self, request, auction_id, fixture_id, group_id):
        try:
            competition = FixtureCompetition.objects.get(
                id=fixture_id, auction_id=auction_id, auction__manager=request.user
            )
            target_group = FixtureGroup.objects.get(id=group_id, competition=competition)
        except (FixtureCompetition.DoesNotExist, FixtureGroup.DoesNotExist):
            return Response(status=status.HTTP_404_NOT_FOUND)

        team_id = request.data.get('team_id')
        try:
            team = Team.objects.get(id=team_id, fixture_competitions=competition)
        except Team.DoesNotExist:
            return Response({'error': 'Team is not part of this tournament.'}, status=400)

        affected_groups = list(FixtureGroup.objects.filter(competition=competition, teams=team))
        for group in affected_groups:
            group.teams.remove(team)
        if target_group not in affected_groups:
            affected_groups.append(target_group)
        target_group.teams.add(team)

        for group in affected_groups:
            FixtureMatch.objects.filter(competition=competition, stage='league', group=group).update(
                is_deleted=True, deleted_at=timezone.now()
            )
            _generate_league_matches(competition, list(group.teams.all()), group=group)

        return Response(FixtureGroupSerializer(FixtureGroup.objects.filter(competition=competition).order_by('order', 'id'), many=True).data)


class FixtureKnockoutSeedProposalView(APIView):
    """Propose a randomized cross-group knockout bracket from current group standings.

    Does not create any matches — the manager reviews/edits the returned pairs and
    submits them through the existing FixtureKnockoutCreateView to commit.
    """
    permission_classes = [IsManagerPermission]

    STAGE_BY_QUALIFIER_COUNT = {2: 'final', 4: 'semi', 8: 'quarter', 16: 'round_of_16', 32: 'round_of_32'}

    def post(self, request, auction_id, fixture_id):
        try:
            competition = FixtureCompetition.objects.get(
                id=fixture_id, auction_id=auction_id, auction__manager=request.user
            )
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        groups = list(competition.groups.all().order_by('order', 'id'))
        if not groups:
            return Response({'error': 'This tournament has no groups.'}, status=400)

        advance = competition.teams_per_group_advance
        group_tables = _fixture_group_tables(competition, request)

        qualifiers = []
        for entry in group_tables:
            for row in entry['table'][:advance]:
                qualifiers.append({
                    'id': row['team_id'],
                    'name': row['team_name'],
                    'group_id': entry['group_id'],
                    'group_name': entry['group_name'],
                })

        stage = self.STAGE_BY_QUALIFIER_COUNT.get(len(qualifiers))
        if not stage:
            return Response({
                'error': (
                    f'{len(qualifiers)} qualifying teams do not form a valid bracket size. '
                    f'Total groups × teams-per-group-advance must equal 2, 4, 8, 16 or 32.'
                )
            }, status=400)

        # Cross-group random pairing: shuffle, then greedily avoid pairing two teams
        # from the same group whenever an alternative is available.
        remaining = list(qualifiers)
        random.shuffle(remaining)
        pairs = []
        while remaining:
            a = remaining.pop(0)
            candidates = [i for i, b in enumerate(remaining) if b['group_id'] != a['group_id']]
            idx = random.choice(candidates) if candidates else 0
            b = remaining.pop(idx)
            pairs.append({'home': a, 'away': b})

        return Response({'stage': stage, 'pairs': pairs})


class FixtureRosterEntryListCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def get_competition(self, request, auction_id, fixture_id):
        return FixtureCompetition.objects.get(
            id=fixture_id,
            auction_id=auction_id,
            auction__manager=request.user,
        )

    def get(self, request, auction_id, fixture_id):
        try:
            competition = self.get_competition(request, auction_id, fixture_id)
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        entries = FixtureRosterEntry.objects.filter(competition=competition).select_related('team', 'player', 'profile')
        return Response(FixtureRosterEntrySerializer(entries, many=True).data)

    def post(self, request, auction_id, fixture_id):
        try:
            competition = self.get_competition(request, auction_id, fixture_id)
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        team_id = request.data.get('team')
        name = request.data.get('name', '').strip()
        player_id = request.data.get('player')
        profile_id = request.data.get('profile')
        if not team_id or not name:
            return Response({'error': 'team and name are required.'}, status=400)
        if not competition.teams.filter(id=team_id).exists():
            return Response({'error': 'Team is not part of this fixture.'}, status=400)

        entry = FixtureRosterEntry.objects.create(
            competition=competition,
            team_id=team_id,
            player_id=player_id or None,
            profile_id=profile_id or None,
            name=name,
            is_custom=not bool(player_id),
            is_active=True,
        )

        if profile_id:
            if competition.status == 'completed':
                from players.rating_engine import handle_competition_completed
                handle_competition_completed(competition)
            else:
                from players.rating_engine import recompute_competition_profiles
                recompute_competition_profiles(competition)

        return Response(FixtureRosterEntrySerializer(entry).data, status=201)


class FixtureRosterEntryDetailView(APIView):
    permission_classes = [IsManagerPermission]

    def patch(self, request, auction_id, fixture_id, entry_id):
        try:
            entry = FixtureRosterEntry.objects.get(
                id=entry_id,
                competition_id=fixture_id,
                competition__auction_id=auction_id,
                competition__auction__manager=request.user,
            )
        except FixtureRosterEntry.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        update_fields = []
        old_profile_id = entry.profile_id
        profile_changed = False
        if 'name' in request.data:
            entry.name = request.data.get('name', '').strip() or entry.name
            update_fields.append('name')
        if 'is_active' in request.data:
            entry.is_active = bool(request.data.get('is_active'))
            update_fields.append('is_active')
        if 'profile' in request.data:
            entry.profile_id = request.data.get('profile') or None
            update_fields.append('profile')
            profile_changed = entry.profile_id != old_profile_id
        if update_fields:
            entry.save(update_fields=update_fields)

        if profile_changed:
            from players.models import PlayerProfile
            from players.rating_engine import (
                handle_competition_completed,
                recompute_competition_profiles,
                recompute_rating,
            )

            if entry.competition.status == 'completed':
                # Re-scans stats for this competition: reassigns badges to whoever is
                # now linked, and recomputes rating for every profile still linked to it.
                handle_competition_completed(entry.competition)
            else:
                recompute_competition_profiles(entry.competition)

            # The profile that just lost this link won't be revisited above (it may no
            # longer be linked to this competition at all) — refresh it explicitly so
            # its cached rating drops the credit it no longer has.
            if old_profile_id and old_profile_id != entry.profile_id:
                old_profile = PlayerProfile.objects.filter(id=old_profile_id).first()
                if old_profile:
                    recompute_rating(old_profile)

        return Response(FixtureRosterEntrySerializer(entry).data)

    def delete(self, request, auction_id, fixture_id, entry_id):
        try:
            entry = FixtureRosterEntry.objects.get(
                id=entry_id,
                competition_id=fixture_id,
                competition__auction_id=auction_id,
                competition__auction__manager=request.user,
            )
        except FixtureRosterEntry.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        entry.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomTournamentListCreateView(APIView):
    permission_classes = [IsManagerPermission]

    def get(self, request):
        tournaments = CustomTournament.objects.filter(manager=request.user)
        serializer = CustomTournamentSerializer(tournaments, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CustomTournamentSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(manager=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomTournamentDetailView(APIView):
    permission_classes = [IsManagerPermission]

    def get_object(self, request, pk):
        try:
            return CustomTournament.objects.get(pk=pk, manager=request.user)
        except CustomTournament.DoesNotExist:
            return None

    def get(self, request, pk):
        tournament = self.get_object(request, pk)
        if not tournament:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(CustomTournamentSerializer(tournament).data)

    def patch(self, request, pk):
        tournament = self.get_object(request, pk)
        if not tournament:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = CustomTournamentSerializer(tournament, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        tournament = self.get_object(request, pk)
        if not tournament:
            return Response(status=status.HTTP_404_NOT_FOUND)
        tournament.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicAuctionListView(APIView):
    """Auctions a spectator can watch.

    This previously filtered on a non-existent `is_active` field, which made it a
    hard 500. Shadow auctions created behind fixtures are excluded — there is
    nothing to watch there — and `?status=active` narrows to what is running right
    now, which is what drives the website's live banner.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        auctions = Auction.objects.filter(is_fixture_only=False)
        status_filter = request.query_params.get('status')
        if status_filter:
            auctions = auctions.filter(status=status_filter)
        return Response(
            AuctionListSerializer(auctions.order_by('-created_at'), many=True).data
        )


class PublicFixtureCompetitionListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        competitions = FixtureCompetition.objects.select_related('auction', 'season', 'winner', 'runner_up').all()
        comp_data = FixtureCompetitionListSerializer(competitions, many=True).data

        custom_tournaments = CustomTournament.objects.all()
        custom_data = CustomTournamentSerializer(custom_tournaments, many=True).data

        combined = comp_data + custom_data
        combined.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return Response(combined)


class PublicFixtureCompetitionDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, fixture_id):
        try:
            competition = FixtureCompetition.objects.get(id=fixture_id)
        except FixtureCompetition.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        goal_stats, defence_stats, stats_meta = _fixture_player_stats(competition, request)
        serializer = FixtureCompetitionDetailSerializer(
            competition,
            context={
                'request': request,
                'table': _fixture_table(competition, request),
                'group_tables': _fixture_group_tables(competition, request),
                'goal_stats': goal_stats,
                'defence_stats': defence_stats,
                'defence_stats_meta': stats_meta,
            }
        )
        return Response(serializer.data)

class PublicCustomTournamentDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            tournament = CustomTournament.objects.get(pk=pk)
        except CustomTournament.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        serializer = CustomTournamentSerializer(tournament, context={'request': request})
        return Response(serializer.data)
class PublicLatestFixtureCompetitionView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        competition = FixtureCompetition.objects.order_by('-created_at').first()
        if not competition:
            return Response(status=status.HTTP_404_NOT_FOUND)

        goal_stats, defence_stats, stats_meta = _fixture_player_stats(competition, request)
        serializer = FixtureCompetitionDetailSerializer(
            competition,
            context={
                'request': request,
                'table': _fixture_table(competition, request),
                'group_tables': _fixture_group_tables(competition, request),
                'goal_stats': goal_stats,
                'defence_stats': defence_stats,
                'defence_stats_meta': stats_meta,
            }
        )
        return Response(serializer.data)
