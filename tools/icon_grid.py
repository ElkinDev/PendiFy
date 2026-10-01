"""The program's icon as text: its own drawing, a round white creature with round ears and a violet scarf
sitting on a notification bubble.

One character per colour, "." transparent. GRIDS[32] is the drawing at 32 by 32 and GRIDS[16] the same figure
drawn again by hand at 16 by 16, not scaled down. tools/make_icon.py writes src/pcnotify/pcnotify.ico and
src/pcnotify/icon.py from these grids; change a cell here, then run the tool.
"""

# The outline colour: every cell of the figure that touches a transparent cell or the grid's border is this one.
OUTLINE = "o"

PALETTE = {
    "o": "#8A5CF0",  # outline: one violet between the page's two brand tones, at least 3:1 on light and dark wallpaper
    "k": "#1E1533",  # eyes (the page's light ink)
    "w": "#FFFFFF",  # snow white
    "t": "#E9DEFB",  # white's shade (the page's light tonal)
    "v": "#6D28D9",  # brand violet (the page's light brand)
    "l": "#C3B1F7",  # light violet (the page's dark brand)
    "d": "#40277C",  # deep violet (the page's dark tonal)
    "s": "#A79FC2",  # soft shadow (the page's dark secondary ink)
}

GRIDS = {
    32: (
        "................................",
        "................................",
        ".........ooo........ooo.........",
        "........owtwo......owtwo........",
        "........owtwo......owtwo........",
        "........owtwoooooooowtwo........",
        ".........owwwwwwwwwwwwo.........",
        "........owwwwwwwwwwwwwwo........",
        ".......owwwwwwwwwwwwwwwwo.......",
        ".......owwwwwwwwwwwwwwwto.......",
        "......owwwwwwwwwwwwwwwwwto......",
        "......owwwwkkwwwwwwkkwwwto......",
        "......owwwwkkwwwwwwkkwwtto......",
        "......owwwwwwwwwwwwwwwwtto......",
        "......ovvvvvvvvvvvvvvvvvvo......",
        "......ovvvvvvvvvvvvvvddddo......",
        "......owwwwwwwwwwwvvdtttto......",
        ".......owwwwwwwwwwvvdttto.......",
        ".......owwwwwwwwwwvtvttto.......",
        "........owwwwwttttttttto........",
        "...oooooooooooooooooooooooooo...",
        "..olwwllllsssssssssssslllllllo..",
        ".olwllllllllllllllllllllllllllo.",
        ".ollllllllllllllllllllllllllllo.",
        ".ollllllllllllllllllllllllllllo.",
        ".ollllllllllllllllllllllllllllo.",
        ".ollllllllllllllllllllllllllllo.",
        "..ollllllllllllllllllsssssssso..",
        "...oolllooooooooooooooooooooo...",
        "....ollo........................",
        "....olo.........................",
        "....oo..........................",
    ),
    16: (
        "...ooo....ooo...",
        "..owtwoooowtwo..",
        ".owwwwwwwwwwwwo.",
        ".owwkwwwwwwkwto.",
        ".owwkwwwwwwkwto.",
        ".owwwwwwwwwwtto.",
        ".ovvvvvvvvvvvvo.",
        ".owwwwwwwvvttto.",
        "..owwwwwwvttto..",
        ".oooooooooooooo.",
        "olwllllllllllllo",
        "ollllllllllllllo",
        "ollllllllllllllo",
        ".ollooooooooooo.",
        ".olo............",
        ".oo.............",
    ),
}
