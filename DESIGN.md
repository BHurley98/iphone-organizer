# Design system

The organizer is a task-focused workspace informed by Impeccable’s Operate guidance (https://github.com/pbakaus/impeccable/blob/main/skill/reference/operate.md). This redesign replaces the earlier dark navy interface.

Use a light neutral canvas for extended visual comparison of colorful phone icons. A pale navigation rail carries stable destinations; real device content leads the workspace. Forest green identifies current selection and primary actions. Muted red identifies deletion, never unrelated controls. One Windows system sans family serves every UI role.

Page boards are functional representations of Home Screen pages with four-column icon grids. A right-side Arrange inspector groups selected-app actions and protections. On narrower windows the inspector shifts above the canvas and navigation becomes a compact horizontal group. Keep page numbers because they identify actual phone placement.

Controls use consistent 8px radii, 38px minimum heights, explicit focus outlines, and short state transitions. Page and modal containers use 16px radii and borders. Real app artwork is read from the phone; folder previews show actual children. Unavailable artwork has a text fallback.

Sorting puts one app and its two decisions in focus. Progress is measured with a native progress element; import tools and Identifiers use disclosure controls. Deletion remains a separate reviewed action. Review dialogs keep exact change lists and explicit authorization.

All colors, spacing, typography, and responsive behavior live in ui/style.css. No external fonts, scripts, or runtime UI dependencies are introduced. Honor reduced-motion preferences. Preserve device logic and the portable packaging boundary.

Page previews reserve exactly six rows of four positions. Empty cells are view-only drop targets. Expanded folders appear in a separate tray so page dimensions remain stable. Live wallpaper is held in browser memory as a temporary local image URL, with readable labels and a plain-view toggle.
