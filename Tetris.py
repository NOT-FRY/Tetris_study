"""
Tetris (single-file) - Bug fixes + debug comments
Run: python Tetris_Python.py
Requires: pygame (pip install pygame)

CHANGELOG (lines referenced below correspond to this file):
- 0010: Added DEBUG flag and instructions how to enable it.
- 0138: Added get_shape_from_bag() to unify piece generation (replaces earlier inconsistent functions).
- 0176: Rewrote clear_rows() to inspect locked_positions directly (fixes delayed clears).
- 0268: Main: unified bag initialization and consistent current/next spawning.
- 0360: HARD_DROP handling: lock -> rebuild grid -> clear_rows -> spawn next using bag.
- 0420: Automatic-drop lock path: same as HARD_DROP (rebuild grid then clear_rows then spawn).

What I changed (short):
- Unified piece generation with a single function get_shape_from_bag(bag).
- clear_rows now checks locked_positions for fullness, removes rows and collapses above rows correctly and supports multiple row clears at once.
- After locking a piece we always rebuild the visible grid from locked_positions BEFORE clearing rows; this prevents the visual-grid/stale-state bug.
- Ensured both hard-drop and auto-drop paths use the same spawn logic (current <- next, next <- new index from bag).
- Added DEBUG prints and inline debug comments explaining where to look.

No author attribution is in this file.
"""

import pygame
import random
import sys

# -----------------------------
# CONFIGURATION
# -----------------------------
CELL_SIZE = 30
COLS = 10
ROWS = 20
BORDER = 20
SIDE_PANEL = 220
FPS = 60

BASE_FALL_SPEED = 0.5
LEVEL_SPEED_DECREMENT = 0.03
MIN_FALL_SPEED = 0.05
LEVEL_UP_LINES = 10

# 12 selectable speed levels: 0.60 (slowest) down to 0.05 (fastest)
SELECTABLE_SPEEDS = [round(0.60 - i * 0.05, 2) for i in range(12)]  # [0.6, 0.55, ..., 0.05]

# Adaptive speed tuning: every ADAPT_BLOCK_WINDOW pieces locked, check lines cleared
ADAPT_BLOCK_WINDOW = 15   # number of pieces between checks
ADAPT_SPEED_STEP  = 0.05  # how much to shift fall_speed per check
SCORING_MAP = {1: 100, 2: 300, 3: 500, 4: 800}
LOSS_Y_THRESHOLD = 0

MOVE_LEFT = pygame.K_LEFT
MOVE_RIGHT = pygame.K_RIGHT
ROTATE = pygame.K_UP
SOFT_DROP = pygame.K_DOWN
HARD_DROP = pygame.K_SPACE
PAUSE_KEY = pygame.K_p
QUIT_KEY = pygame.K_ESCAPE

# 0010 - DEBUG flag (enable for terminal debugging)
DEBUG = False  # set to True to see debug prints in console

WIDTH = CELL_SIZE * COLS + BORDER * 2
HEIGHT = CELL_SIZE * ROWS + BORDER * 2

pygame.init()
SCREEN = pygame.display.set_mode((WIDTH + SIDE_PANEL, HEIGHT))
pygame.display.set_caption("Tetris")
CLOCK = pygame.time.Clock()

# Colors and shapes
BLACK = (10, 10, 10)
WHITE = (245, 245, 245)
GREY = (80, 80, 80)
CYAN = (0, 200, 215)
YELLOW = (255, 200, 0)
MAGENTA = (200, 50, 200)
LIME = (120, 255, 100)
RED = (220, 40, 40)
BLUE = (70, 120, 255)
ORANGE = (255, 130, 40)

SHAPES = [
    [[1, 1, 1, 1]],
    [[1, 1], [1, 1]],
    [[0, 1, 0], [1, 1, 1]],
    [[0, 1, 1], [1, 1, 0]],
    [[1, 1, 0], [0, 1, 1]],
    [[1, 0, 0], [1, 1, 1]],
    [[0, 0, 1], [1, 1, 1]],
]
SHAPE_COLORS = [CYAN, YELLOW, MAGENTA, LIME, RED, BLUE, ORANGE]

FONT = pygame.font.SysFont("Consolas", 20, bold=True)
BIG_FONT = pygame.font.SysFont("Consolas", 44, bold=True)
SMALL_FONT = pygame.font.SysFont("Consolas", 14)
MED_FONT  = pygame.font.SysFont("Consolas", 28, bold=True)

# Feedback messages shown after each 30-block adaptive check
GOOD_MESSAGES = ["GOOD JOB!", "YOU'RE DOING GREAT!", "WELL DONE!", "NICE WORK!"]
POOR_MESSAGES = ["KEEP IT UP!", "YOU CAN DO IT!", "IT'S GONNA BE FINE!"]

# -----------------------------
# Helpers
# -----------------------------

def rotate(shape):
    return [list(row) for row in zip(*shape[::-1])]


def create_grid(locked_positions=None):
    # create grid from locked_positions (single source of truth)
    if locked_positions is None:
        locked_positions = {}
    grid = [[BLACK for _ in range(COLS)] for _ in range(ROWS)]
    for (c, r), color in locked_positions.items():
        if 0 <= r < ROWS and 0 <= c < COLS:
            grid[r][c] = color
    return grid


class Piece:
    def __init__(self, x, y, shape_idx):
        self.x = x
        self.y = y
        self.shape_idx = shape_idx
        self.shape = SHAPES[shape_idx]
        self.color = SHAPE_COLORS[shape_idx]
        self.rotation = 0

    def image(self):
        rot = self.shape
        for _ in range(self.rotation % 4):
            rot = rotate(rot)
        return rot


def convert_shape_format(piece):
    positions = []
    shape = piece.image()
    for i, row in enumerate(shape):
        for j, val in enumerate(row):
            if val:
                positions.append((piece.x + j, piece.y + i))
    return positions


def valid_space(piece, grid):
    accepted = [[(c, r) for c in range(COLS) if grid[r][c] == BLACK] for r in range(ROWS)]
    accepted = [c for row in accepted for c in row]
    formatted = convert_shape_format(piece)
    for x, y in formatted:
        if x < 0 or x >= COLS or y >= ROWS:
            return False
        if (x, y) not in accepted and y >= 0:
            return False
    return True


def check_lost(locked_positions):
    for (c, r) in locked_positions:
        if r <= LOSS_Y_THRESHOLD:
            return True
    return False


# 0138 - unified bag pop function
def get_shape_from_bag(bag):
    """Pop one index from the bag; refill and reshuffle if needed.

    This ensures both hard-drop and auto-drop flows draw from the same bag state.
    """
    if bag is None or not bag:
        bag = list(range(len(SHAPES)))
        random.shuffle(bag)
        if DEBUG:
            print("[DEBUG] Refilled bag:", bag)
    idx = bag.pop()
    return idx, bag


# 0176 - clear_rows now uses locked positions directly and supports multiple clears
def clear_rows(grid, locked):
    """Clear complete rows by inspecting locked_positions directly.

    Fixed algorithm for multiple-row clears:
    1. First detect all full rows and collect them in a list `full_rows`.
    2. Remove all locked cells that lie on those rows.
    3. For every remaining locked cell, compute how many cleared rows are *below* it
       (i.e. with a greater row index). Shift the cell down by that count.

    This avoids problems caused by deleting and shifting rows inside the same loop
    (which can cause some rows to be skipped when multiple rows are cleared).
    """
    # Step 1: detect full rows
    full_rows = []
    for r in range(ROWS - 1, -1, -1):
        full = True
        for c in range(COLS):
            if (c, r) not in locked:
                full = False
                break
        if full:
            full_rows.append(r)

    # If no full rows, quick return
    if not full_rows:
        return 0

    # Step 2: remove locked cells that are on full rows
    for r in full_rows:
        for c in range(COLS):
            if (c, r) in locked:
                del locked[(c, r)]

    # Step 3: shift remaining blocks down by number of cleared rows below them
    # We create a new dict because modifying 'locked' while iterating it is unsafe.
    new_locked = {}
    for (x, y), color in sorted(locked.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        # count how many cleared rows are below this block
        shift = 0
        for cleared_row in full_rows:
            if cleared_row > y:
                shift += 1
        new_y = y + shift
        new_locked[(x, new_y)] = color

    # Replace locked with new_locked contents
    locked.clear()
    locked.update(new_locked)

    lines_cleared = len(full_rows)
    if DEBUG:
        print(f"[DEBUG] Cleared rows: {sorted(full_rows)} -> {lines_cleared} total")
    return lines_cleared



def draw_grid(surface, grid):
    for r in range(ROWS):
        for c in range(COLS):
            rect = pygame.Rect(BORDER + c * CELL_SIZE, BORDER + r * CELL_SIZE, CELL_SIZE, CELL_SIZE)
            pygame.draw.rect(surface, grid[r][c], rect)
            pygame.draw.rect(surface, GREY, rect, 1)


def draw_side_panel(surface, score, level, next_piece, fall_speed=0.5):
    panel_x = WIDTH + 10
    pygame.draw.line(surface, WHITE, (WIDTH, 0), (WIDTH, HEIGHT), 2)
    title = BIG_FONT.render("TETRIS", True, WHITE)
    surface.blit(title, (panel_x + 10, 10))
    score_label = FONT.render("SCORE", True, WHITE)
    score_val = FONT.render(str(score), True, WHITE)
    surface.blit(score_label, (panel_x + 10, 70))
    surface.blit(score_val, (panel_x + 10, 100))
    level_label = FONT.render("LEVEL", True, WHITE)
    level_val = FONT.render(str(level), True, WHITE)
    surface.blit(level_label, (panel_x + 10, 140))
    surface.blit(level_val, (panel_x + 10, 170))

    # --- Real-time speed display ---
    spd_label = FONT.render("SPEED", True, WHITE)
    surface.blit(spd_label, (panel_x + 10, 210))
    # Map fall_speed interval to a 1-12 level number for readability
    spd_level = max(1, min(12, round((0.60 - fall_speed) / 0.05) + 1))
    spd_val   = FONT.render(f"{fall_speed:.2f}s  (L{spd_level})", True, CYAN)
    surface.blit(spd_val, (panel_x + 10, 238))
    # Visual bar: 12 segments, filled = current speed level
    bar_x, bar_y, seg_w, seg_h, gap = panel_x + 10, 265, 13, 10, 2
    for s in range(12):
        seg_rect = pygame.Rect(bar_x + s * (seg_w + gap), bar_y, seg_w, seg_h)
        color = CYAN if s < spd_level else GREY
        pygame.draw.rect(surface, color, seg_rect, border_radius=2)

    np_label = FONT.render("NEXT", True, WHITE)
    surface.blit(np_label, (panel_x + 10, 285))
    if next_piece is not None:
        shape = next_piece.image()
        start_x = panel_x + 40
        start_y = 315
        for i, row in enumerate(shape):
            for j, val in enumerate(row):
                if val:
                    rect = pygame.Rect(start_x + j * CELL_SIZE, start_y + i * CELL_SIZE, CELL_SIZE, CELL_SIZE)
                    pygame.draw.rect(surface, next_piece.color, rect)
                    pygame.draw.rect(surface, WHITE, rect, 1)
    cmds = [
        "COMMANDS:",
        "Left  - Move Left",
        "Right - Move Right",
        "Up    - Rotate",
        "Down  - Soft Drop",
        "Space - Hard Drop",
        "P     - Pause",
        "Esc   - Quit"
    ]
    y = 420
    for line in cmds:
        text = SMALL_FONT.render(line, True, WHITE)
        surface.blit(text, (panel_x + 10, y))
        y += 24


def draw_window(surface, grid, score, level, next_piece, fall_speed=0.5,
                toast_text="", toast_alpha=0, toast_good=True):
    surface.fill(BLACK)
    draw_grid(surface, grid)
    draw_side_panel(surface, score, level, next_piece, fall_speed)

    # --- Toast message overlay ---
    if toast_text and toast_alpha > 0:
        alpha = max(0, min(255, int(toast_alpha)))
        color = LIME if toast_good else ORANGE
        msg_surf = MED_FONT.render(toast_text, True, color)
        # Semi-transparent dark backing pill
        pad_x, pad_y = 18, 10
        pill = pygame.Surface(
            (msg_surf.get_width() + pad_x * 2, msg_surf.get_height() + pad_y * 2),
            pygame.SRCALPHA
        )
        bg_alpha = min(200, alpha)
        pygame.draw.rect(pill, (10, 10, 10, bg_alpha), pill.get_rect(), border_radius=12)
        pill.blit(msg_surf, (pad_x, pad_y))
        # Set overall alpha for fade
        pill.set_alpha(alpha)
        cx = (WIDTH) // 2 - pill.get_width() // 2
        cy = HEIGHT // 2 - pill.get_height() // 2
        surface.blit(pill, (cx, cy))

    pygame.display.update()


def start_screen():
    SCREEN.fill(BLACK)
    title = BIG_FONT.render("", True, WHITE)
    prompt = FONT.render("Press any key to start", True, WHITE)
    text_rect = prompt.get_rect(center=(SCREEN.get_width() / 2, HEIGHT / 2))
    SCREEN.blit(title, (WIDTH // 2 - title.get_width() // 2, HEIGHT // 2 - 80))
    SCREEN.blit(prompt, text_rect)
    pygame.display.update()
    waiting = True
    while waiting:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                waiting = False

def condition_select_screen():
    """
    Display a pre-game selection screen with 3 clickable conditions.
    Also shows an "Options" listbox (bottom-left) to manually pick a starting speed.
    Returns (chosen_condition, chosen_speed) where chosen_speed is a float or None.
    """
    conditions = ["Condition A", "Condition B", "Condition C"]
    buttons = []

    # --- Options / speed listbox state ---
    options_open        = False          # whether the dropdown is expanded
    selected_speed_idx  = None           # index into SELECTABLE_SPEEDS (None = no override)

    # Listbox geometry (bottom-left of the full window)
    LB_X      = 10
    LB_Y      = HEIGHT - 38             # collapsed button sits near bottom
    LB_W      = 180
    LB_H      = 30                       # height of one row
    LB_ROWS   = len(SELECTABLE_SPEEDS)  # 12

    while True:
        SCREEN.fill((0, 0, 0))
        title = BIG_FONT.render("SELECT CONDITION", True, WHITE)
        SCREEN.blit(title, (WIDTH // 1.25 - title.get_width() // 2, HEIGHT // 4))

        mouse_pos = pygame.mouse.get_pos()

        # --- Draw condition buttons ---
        buttons.clear()
        for i, cond in enumerate(conditions):
            text = FONT.render(cond, True, WHITE)
            text_rect = text.get_rect(center=(WIDTH // 1.25, HEIGHT // 2 + i * 70))
            button_rect = pygame.Rect(
                text_rect.left - 20, text_rect.top - 10,
                text_rect.width + 40, text_rect.height + 20
            )
            if button_rect.collidepoint(mouse_pos):
                pygame.draw.rect(SCREEN, (200, 200, 0), button_rect, border_radius=10)
            else:
                pygame.draw.rect(SCREEN, (50, 50, 50), button_rect, border_radius=10)
            pygame.draw.rect(SCREEN, WHITE, button_rect, 2, border_radius=10)
            SCREEN.blit(text, text_rect)
            buttons.append((button_rect, cond))

        # --- Draw "Options" collapsed button / listbox ---
        # Label above the box
        opt_label = SMALL_FONT.render("Options  (Starting Speed)", True, (180, 180, 180))
        SCREEN.blit(opt_label, (LB_X, LB_Y - 18))

        if selected_speed_idx is not None:
            collapsed_text = f"Speed: {SELECTABLE_SPEEDS[selected_speed_idx]:.2f}"
        else:
            collapsed_text = "Speed: default"

        collapsed_rect = pygame.Rect(LB_X, LB_Y, LB_W, LB_H)
        pygame.draw.rect(SCREEN, (50, 50, 80), collapsed_rect, border_radius=4)
        pygame.draw.rect(SCREEN, WHITE, collapsed_rect, 1, border_radius=4)
        ct = SMALL_FONT.render(collapsed_text + "  v", True, WHITE)
        SCREEN.blit(ct, (LB_X + 6, LB_Y + 8))

        # Expanded dropdown (drawn on top of everything else)
        if options_open:
            list_top = LB_Y - LB_H * LB_ROWS   # open upward
            for j, spd in enumerate(SELECTABLE_SPEEDS):
                row_rect = pygame.Rect(LB_X, list_top + j * LB_H, LB_W, LB_H)
                if j == selected_speed_idx:
                    bg = (80, 80, 160)
                elif row_rect.collidepoint(mouse_pos):
                    bg = (60, 60, 100)
                else:
                    bg = (30, 30, 60)
                pygame.draw.rect(SCREEN, bg, row_rect)
                pygame.draw.rect(SCREEN, GREY, row_rect, 1)
                label = f"Level {j+1:>2}  —  {spd:.2f} s"
                rt = SMALL_FONT.render(label, True, WHITE)
                SCREEN.blit(rt, (LB_X + 6, list_top + j * LB_H + 8))

        pygame.display.update()

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.MOUSEBUTTONDOWN:
                click = pygame.mouse.get_pos()

                # Toggle dropdown
                if collapsed_rect.collidepoint(click):
                    options_open = not options_open
                    continue

                # Click inside open dropdown
                if options_open:
                    list_top = LB_Y - LB_H * LB_ROWS
                    for j in range(LB_ROWS):
                        row_rect = pygame.Rect(LB_X, list_top + j * LB_H, LB_W, LB_H)
                        if row_rect.collidepoint(click):
                            selected_speed_idx = j
                            options_open = False
                            break
                    else:
                        options_open = False  # clicked outside → close
                    continue

                # Condition button click
                for rect, cond in buttons:
                    if rect.collidepoint(click):
                        chosen_speed = (SELECTABLE_SPEEDS[selected_speed_idx]
                                        if selected_speed_idx is not None else None)
                        return cond, chosen_speed

def adaptive_speed_adjust(fall_speed, lines_in_window):
    """Called every ADAPT_BLOCK_WINDOW pieces.

    - lines_in_window >= 5 → increase speed (decrease interval by ADAPT_SPEED_STEP)
    - lines_in_window <  3 → decrease speed (increase interval by ADAPT_SPEED_STEP)
    - 3 <= lines < 5       → no change

    Speed is clamped to [MIN_FALL_SPEED, 0.60].
    """
    if lines_in_window >= 5:
        new_speed = fall_speed - ADAPT_SPEED_STEP   # faster
        if DEBUG:
            print(f"[ADAPT] {lines_in_window} lines cleared → speed up: {fall_speed:.2f} → {new_speed:.2f}")
    elif lines_in_window < 3:
        new_speed = fall_speed + ADAPT_SPEED_STEP   # slower
        if DEBUG:
            print(f"[ADAPT] {lines_in_window} lines cleared → speed down: {fall_speed:.2f} → {new_speed:.2f}")
    else:
        new_speed = fall_speed  # keep current
        if DEBUG:
            print(f"[ADAPT] {lines_in_window} lines cleared → speed unchanged: {fall_speed:.2f}")
    return round(max(MIN_FALL_SPEED, min(0.60, new_speed)), 2)


def main(condition="Condition A", manual_speed=None):
    # -----------------------------
    # Apply condition settings
    # -----------------------------
    global BASE_FALL_SPEED, SCORING_MAP

    if condition == "press to Play":  # Adaptive
        BASE_FALL_SPEED = 0.6
        SCORING_MAP = {1: 100, 2: 200, 3: 400, 4: 800}
    elif condition == "Condition B":  # Easy
        BASE_FALL_SPEED = 0.8
        SCORING_MAP = {1: 120, 2: 300, 3: 500, 4: 1000}
    elif condition == "Condition C":  # Hard
        BASE_FALL_SPEED = 0.05
        SCORING_MAP = {1: 150, 2: 400, 3: 700, 4: 1200}

    # Manual speed override from Options listbox takes priority
    if manual_speed is not None:
        BASE_FALL_SPEED = manual_speed

    if DEBUG:
        print(f"[DEBUG] Game starting with {condition}: BASE_FALL_SPEED={BASE_FALL_SPEED}")
    # locked_positions stores cells that are fixed in place: (x,y) -> color
    locked_positions = {}

    # 0268 - Bag initialization and first two pieces (explicit indices -> Piece objects)
    bag = list(range(len(SHAPES)))
    random.shuffle(bag)
    idx, bag = get_shape_from_bag(bag)
    current_piece = Piece(COLS // 2 - 2, -2, idx)
    idx, bag = get_shape_from_bag(bag)
    next_piece = Piece(COLS // 2 - 2, -2, idx)

    fall_time = 0
    fall_speed = BASE_FALL_SPEED
    level = 1
    score = 0
    lines_cleared_total = 0
    run = True

    # --- Adaptive speed tracking (per 30-block window) ---
    adapt_blocks_since_check = 0   # pieces locked since last window check
    adapt_lines_in_window    = 0   # lines cleared within the current window

    # --- Toast / feedback message state ---
    TOAST_DURATION = 2.5           # seconds the message stays visible
    toast_text     = ""
    toast_alpha    = 0             # 0-255, driven down each frame for fade-out
    toast_good     = True          # True = good (green), False = poor (orange)

    while run:
        grid = create_grid(locked_positions)
        dt = CLOCK.tick(FPS) / 1000.0
        fall_time += dt
        # fall_speed is managed by adaptive_speed_adjust() and initial setup;
        # we no longer recalculate it every tick from BASE_FALL_SPEED so that
        # manual overrides and adaptive adjustments are preserved.

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == QUIT_KEY:
                    pygame.quit()
                    sys.exit()
                if event.key == PAUSE_KEY:
                    paused = True
                    pause_text = FONT.render("PAUSED - Press P to resume", True, WHITE)
                    SCREEN.blit(pause_text, (WIDTH // 2 - pause_text.get_width() // 2, HEIGHT // 2))
                    pygame.display.update()
                    while paused:
                        for e in pygame.event.get():
                            if e.type == pygame.QUIT:
                                pygame.quit()
                                sys.exit()
                            if e.type == pygame.KEYDOWN and e.key == PAUSE_KEY:
                                paused = False
                if event.key == MOVE_LEFT:
                    current_piece.x -= 1
                    if not valid_space(current_piece, grid):
                        current_piece.x += 1
                elif event.key == MOVE_RIGHT:
                    current_piece.x += 1
                    if not valid_space(current_piece, grid):
                        current_piece.x -= 1
                elif event.key == ROTATE:
                    current_piece.rotation = (current_piece.rotation + 1) % 4
                    if not valid_space(current_piece, grid):
                        current_piece.rotation = (current_piece.rotation - 1) % 4
                elif event.key == SOFT_DROP:
                    current_piece.y += 1
                    if not valid_space(current_piece, grid):
                        current_piece.y -= 1
                elif event.key == HARD_DROP:
                    # 0360 - HARD DROP path: lock, rebuild grid, clear_rows, spawn next
                    while valid_space(current_piece, grid):
                        current_piece.y += 1
                    current_piece.y -= 1
                    for pos in convert_shape_format(current_piece):
                        if pos[1] >= 0:
                            locked_positions[(pos[0], pos[1])] = current_piece.color
                    # Rebuild the grid AFTER we modify locked_positions so clear_rows sees the true state
                    grid = create_grid(locked_positions)  # DEBUG: important fix
                    cleared = clear_rows(grid, locked_positions)
                    if cleared > 0:
                        lines_cleared_total += cleared
                        score += SCORING_MAP.get(cleared, cleared * 200) * level
                        level = 1 + lines_cleared_total // LEVEL_UP_LINES
                    # --- Adaptive speed: count this locked piece ---
                    adapt_blocks_since_check += 1
                    adapt_lines_in_window    += cleared
                    if adapt_blocks_since_check >= ADAPT_BLOCK_WINDOW:
                        fall_speed = adaptive_speed_adjust(fall_speed, adapt_lines_in_window)
                        if adapt_lines_in_window >= 5:
                            toast_text = random.choice(GOOD_MESSAGES)
                            toast_good = True
                        elif adapt_lines_in_window < 3:
                            toast_text = random.choice(POOR_MESSAGES)
                            toast_good = False
                        else:
                            toast_text = ""
                        toast_alpha = 255
                        adapt_blocks_since_check = 0
                        adapt_lines_in_window    = 0
                    # Spawn pieces: current becomes previous 'next', and next is drawn from bag
                    current_piece = next_piece
                    idx, bag = get_shape_from_bag(bag)
                    next_piece = Piece(COLS // 2 - 2, -2, idx)
                    if DEBUG:
                        print(f"[DEBUG] Hard drop locked. new next idx={idx}")

        # Automatic falling
        if fall_time > fall_speed:
            fall_time = 0
            current_piece.y += 1
            if not valid_space(current_piece, grid):
                current_piece.y -= 1
                # Lock piece into locked_positions
                for pos in convert_shape_format(current_piece):
                    if pos[1] >= 0:
                        locked_positions[(pos[0], pos[1])] = current_piece.color
                # 0420 - rebuild grid then clear (same logic as hard-drop to avoid mismatch)
                grid = create_grid(locked_positions)  # DEBUG: ensure clear_rows sees locked state
                cleared = clear_rows(grid, locked_positions)
                if cleared > 0:
                    lines_cleared_total += cleared
                    score += SCORING_MAP.get(cleared, cleared * 200) * level
                    level = 1 + lines_cleared_total // LEVEL_UP_LINES
                # --- Adaptive speed: count this locked piece ---
                adapt_blocks_since_check += 1
                adapt_lines_in_window    += cleared
                if adapt_blocks_since_check >= ADAPT_BLOCK_WINDOW:
                    fall_speed = adaptive_speed_adjust(fall_speed, adapt_lines_in_window)
                    if adapt_lines_in_window >= 5:
                        toast_text = random.choice(GOOD_MESSAGES)
                        toast_good = True
                    elif adapt_lines_in_window < 3:
                        toast_text = random.choice(POOR_MESSAGES)
                        toast_good = False
                    else:
                        toast_text = ""
                    toast_alpha = 255
                    adapt_blocks_since_check = 0
                    adapt_lines_in_window    = 0
                # Spawn next
                current_piece = next_piece
                idx, bag = get_shape_from_bag(bag)
                next_piece = Piece(COLS // 2 - 2, -2, idx)
                if DEBUG:
                    print(f"[DEBUG] Auto-drop locked. new next idx={idx}")

        # Render moving piece on top of grid (visual only)
        positions = convert_shape_format(current_piece)
        for x, y in positions:
            if y >= 0:
                grid[y][x] = current_piece.color

        # Fade the toast out over time
        if toast_alpha > 0:
            toast_alpha = max(0, toast_alpha - (255 / (TOAST_DURATION * FPS)))

        draw_window(SCREEN, grid, score, level, next_piece,
                    fall_speed, toast_text, toast_alpha, toast_good)

        if check_lost(locked_positions):
            run = False

    # Game Over
    SCREEN.fill(BLACK)
    over = BIG_FONT.render("GAME OVER", True, WHITE)
    score_txt = FONT.render(f"Score: {score}", True, WHITE)
    score_rect = score_txt.get_rect(center=(SCREEN.get_width() // 2, HEIGHT // 2 + 50))
    over_rect = over.get_rect(center=(SCREEN.get_width() // 2, HEIGHT // 2))
    SCREEN.blit(over, over_rect)
    SCREEN.blit(score_txt, score_rect)
    pygame.display.update()
    pygame.time.delay(3000)


if __name__ == "__main__":
    start_screen()
    chosen_condition, chosen_speed = condition_select_screen()  # 👈 show the menu
    main(chosen_condition, chosen_speed)  # 👈 start game with chosen condition + speed