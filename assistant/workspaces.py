"""Tool-scoped AI workspaces. Selecting a workspace narrows which assistant
tools are exposed to the model and swaps in a workspace system-prompt
fragment + suggestion chips — everything else about the chat engine
(provider, streaming, memory) stays the same across workspaces.

`coming_soon` lists actions that still need genuinely new backend endpoints
(not just a new tool wrapper) or were deliberately excluded as too risky for
free-text tool-calling (live auction control/bidding, committing a knockout
bracket, editing post-creation match-day/format settings). They're surfaced
in the UI as disabled/greyed suggestions so the gap stays visible.
"""

_GENERAL_TOOLS = [
    'list_auctions', 'create_auction', 'get_auction_details',
    'list_teams', 'get_team_details', 'create_team', 'update_team',
    'list_tournaments', 'get_tournament_status', 'get_fixture_player_stats',
    'list_matches', 'find_matches', 'propose_knockout_seeding', 'update_tournament_settings',
    'create_tournament', 'publish_match_result',
    'list_custom_tournaments', 'get_custom_tournament', 'create_custom_tournament', 'update_custom_tournament',
    'find_best_players', 'list_player_applications', 'get_player_application', 'get_player_card',
    'approve_player', 'unverify_player', 'reject_player_application',
]

WORKSPACES = [
    {
        'id': 'general',
        'label': 'Chats',
        'icon': 'message-circle',
        'tools': _GENERAL_TOOLS,
        'prompt_extra': '',
        'suggestions': [
            'What tournaments are currently ongoing?',
            'Who are the top 5 rated players?',
            'Create a tournament called "Weekend Cup" with teams Alpha, Beta, Gamma, Delta',
        ],
        'coming_soon': [],
    },
    {
        'id': 'fixture',
        'label': 'Fixtures',
        'icon': 'calendar-days',
        'tools': [
            'list_tournaments', 'get_tournament_status', 'get_fixture_player_stats',
            'list_matches', 'find_matches', 'propose_knockout_seeding', 'update_tournament_settings',
            'create_tournament', 'publish_match_result',
        ],
        'prompt_extra': (
            "You're focused on the Fixtures workspace: creating tournament schedules, checking "
            "match days/standings/player stats, proposing knockout seeding, and publishing results."
        ),
        'suggestions': [
            'Generate a round-robin tournament for 6 teams',
            "Show today's matches",
            'Show the standings for a tournament',
            'Publish a match result',
        ],
        'coming_soon': ['Add a match day', 'Commit a knockout bracket', 'Edit match-day/format settings'],
    },
    {
        'id': 'auction',
        'label': 'Auction',
        'icon': 'gavel',
        'tools': ['list_auctions', 'create_auction', 'get_auction_details'],
        'prompt_extra': (
            "You're focused on the Auction workspace: creating auctions and reading their config/state. "
            "Live control (start/pause/next player) and bidding aren't available through chat — those stay "
            "in the live auction room."
        ),
        'suggestions': [
            'Create an auction for these teams',
            'List my auctions',
            'Show the details of an auction',
        ],
        'coming_soon': ['Start Auction', 'Close Auction', 'Assign Player', 'Undo Last Bid'],
    },
    {
        'id': 'teams',
        'label': 'Teams',
        'icon': 'users',
        'tools': ['list_teams', 'get_team_details', 'create_team', 'update_team'],
        'prompt_extra': (
            "You're focused on the Teams workspace. Only set a captain username/password on create_team if "
            "the manager explicitly gave you both — never invent or guess credentials."
        ),
        'suggestions': [
            'Create a team',
            'List my teams',
            "Show a team's details",
        ],
        'coming_soon': ['Transfer Player', 'Generate Invite Link'],
    },
    {
        'id': 'players',
        'label': 'Players',
        'icon': 'user',
        'tools': [
            'find_best_players', 'list_player_applications', 'get_player_application', 'get_player_card',
            'approve_player', 'unverify_player', 'reject_player_application',
        ],
        'prompt_extra': (
            "You're focused on the Players workspace: finding rated players, viewing approved players' "
            "Player Cards, and reviewing applications. Rejecting an application marks it rejected, it does "
            "not delete it — restate what you're about to do and get a clear yes before approving or "
            "rejecting."
        ),
        'suggestions': [
            'Who are the top 5 rated players?',
            'Show pending player applications',
            "Show a player's Player Card",
        ],
        'coming_soon': [],
    },
    {
        'id': 'tournament',
        'label': 'Tournament',
        'icon': 'trophy',
        'tools': [
            'list_tournaments', 'get_tournament_status', 'create_tournament', 'update_tournament_settings',
            'list_custom_tournaments', 'get_custom_tournament', 'create_custom_tournament', 'update_custom_tournament',
        ],
        'prompt_extra': (
            "You're focused on the Tournament workspace: real bracket tournaments (create_tournament) and "
            "lightweight custom tournament result records (create_custom_tournament) — these are two separate "
            "things, don't mix them up."
        ),
        'suggestions': [
            'Create a tournament with 4 teams',
            'What tournaments are ongoing?',
            'Record a custom tournament result',
        ],
        'coming_soon': [],
    },
    {
        'id': 'results',
        'label': 'Results',
        'icon': 'bar-chart-3',
        'tools': ['list_matches', 'find_matches', 'publish_match_result', 'get_fixture_player_stats', 'get_tournament_status'],
        'prompt_extra': "You're focused on the Results workspace: publishing match results and reviewing standings/player stats.",
        'suggestions': [
            'Publish a match result',
            'Show recent results for a tournament',
            'Show top scorers for a tournament',
        ],
        'coming_soon': [],
    },
]

_BY_ID = {w['id']: w for w in WORKSPACES}


def get_workspace(workspace_id):
    return _BY_ID.get(workspace_id) or _BY_ID['general']


def public_workspaces():
    """Client-facing shape — omits prompt_extra."""
    return [
        {k: v for k, v in w.items() if k != 'prompt_extra' and k != 'tools'}
        for w in WORKSPACES
    ]
