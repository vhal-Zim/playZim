"""Interfaz Textual: integra player, library, history, cache, mpris y el ecualizador."""

from __future__ import annotations

import asyncio
from typing import Optional

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from ytaudio import resolver
from ytaudio.cache import AudioCache
from ytaudio.config import ensure_dirs, load_settings, save_settings
from ytaudio.history import History
from ytaudio.library import Library
from ytaudio.models import PlaybackState, PlayerStatus, RepeatMode, Track
from ytaudio.mpris import MprisService
from ytaudio.player import PlayerEngine
from ytaudio.visualizer import EqualizerWidget

# Paleta exacta del rediseño (ver Rediseño barra de buffers CLI/.../README.md §2.1).
_BG_PANEL = "#0e0d0c"
_TEXT_PRIMARY = "#fbf9f8"
_TEXT_SECONDARY = "#a19a97"
_TEXT_TERTIARY = "#6d6663"
_RULE_COLOR = "#262220"
_ICON_COLOR = "#ec3013"
_PROGRESS_DONE = "#ec3013"
_PROGRESS_HEAD = "#ffd0c3"
_AXIS_RAIL = "#26221f"

_TAB_LABELS = [("lib", "1 Biblioteca"), ("queue", "2 Cola"), ("hist", "3 Historial")]
_SHORTCUTS_TEXT = "space pausa    ] siguiente    f buscar    q salir"


def fmt_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Fila de now-playing, barra de progreso y fila de pestañas/atajos (§2.3-2.5)
# ---------------------------------------------------------------------------


class NowPlayingRow(Horizontal):
    DEFAULT_CSS = f"""
    NowPlayingRow {{
        height: 1;
        background: {_BG_PANEL};
    }}
    NowPlayingRow > #np-left {{
        width: 1fr;
    }}
    NowPlayingRow > #np-right {{
        width: auto;
        content-align: right middle;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(id="np-left")
        yield Static(id="np-right")

    def show_idle(self, message: str) -> None:
        self.query_one("#np-left", Static).update(f"[{_TEXT_SECONDARY}]{escape(message)}[/]")
        self.query_one("#np-right", Static).update("")

    def show_status(self, status: PlayerStatus) -> None:
        track = status.track
        assert track is not None
        icon = "▶" if status.state == PlaybackState.PLAYING else "❚❚"

        left = f"[bold {_ICON_COLOR}]{icon}[/]  [bold {_TEXT_PRIMARY}]{escape(track.title)}[/]"
        if track.uploader:
            left += f"  [{_TEXT_TERTIARY}]—[/]  [{_TEXT_SECONDARY}]{escape(track.uploader)}[/]"
        self.query_one("#np-left", Static).update(left)

        flags = []
        if status.shuffle:
            flags.append("SHUFFLE")
        if status.repeat == RepeatMode.ONE:
            flags.append("REPEAT ONE")
        elif status.repeat == RepeatMode.ALL:
            flags.append("REPEAT ALL")
        right = f"[{_TEXT_TERTIARY}]{' · '.join(flags)}[/]  " if flags else ""
        right += f"[{_TEXT_SECONDARY}]VOL[/] [bold {_TEXT_PRIMARY}]{status.volume}[/]"
        self.query_one("#np-right", Static).update(right)


class ProgressRow(Static):
    """Eje de progreso a todo el ancho, con tiempos en los extremos (§2.4)."""

    DEFAULT_CSS = f"""
    ProgressRow {{
        height: 1;
        width: 1fr;
        background: {_BG_PANEL};
    }}
    """

    position: reactive[float] = reactive(0.0)
    duration: reactive[Optional[float]] = reactive(None)

    def watch_position(self) -> None:
        self._redraw()

    def watch_duration(self) -> None:
        self._redraw()

    def on_resize(self, event) -> None:
        self._redraw()

    def _redraw(self) -> None:
        width = self.size.width
        if not width:
            return
        pos_label = fmt_time(self.position)
        dur_label = fmt_time(self.duration)
        rail_width = max(1, width - len(pos_label) - len(dur_label) - 2)

        frac = 0.0
        if self.duration and self.duration > 0:
            frac = max(0.0, min(1.0, self.position / self.duration))
        filled = int(round(frac * rail_width))
        head = min(filled, rail_width - 1)

        rail = []
        for i in range(rail_width):
            if i == head:
                rail.append(f"[{_PROGRESS_HEAD}]█[/]")
            elif i < filled:
                rail.append(f"[{_PROGRESS_DONE}]█[/]")
            else:
                rail.append(f"[{_AXIS_RAIL}]█[/]")

        markup = f"[{_TEXT_PRIMARY}]{pos_label}[/] {''.join(rail)} [{_TEXT_TERTIARY}]{dur_label}[/]"
        self.update(markup)


class TabsShortcutsRow(Horizontal):
    DEFAULT_CSS = f"""
    TabsShortcutsRow {{
        height: 2;
        border-top: solid {_RULE_COLOR};
    }}
    TabsShortcutsRow > #tabs-left {{
        width: 1fr;
    }}
    TabsShortcutsRow > #tabs-right {{
        width: auto;
        content-align: right middle;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(id="tabs-left")
        yield Static(f"[{_TEXT_TERTIARY}]{_SHORTCUTS_TEXT}[/]", id="tabs-right")

    def update_active(self, active_id: str) -> None:
        parts = []
        for tab_id, label in _TAB_LABELS:
            if tab_id == active_id:
                parts.append(f"[bold {_TEXT_PRIMARY} underline]{label}[/]")
            else:
                parts.append(f"[{_TEXT_TERTIARY}]{label}[/]")
        self.query_one("#tabs-left", Static).update("  ".join(parts))


# ---------------------------------------------------------------------------
# Pantallas modales
# ---------------------------------------------------------------------------


class RenameScreen(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    RenameScreen { align: center middle; }
    #rename-box { width: 60; height: auto; border: round $accent; padding: 1 2; }
    """

    def __init__(self, current_title: str) -> None:
        super().__init__()
        self._current_title = current_title

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-box"):
            yield Label("Nuevo título (Enter=confirmar, Esc=cancelar)")
            yield Input(value=self._current_title, id="rename-input")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def key_escape(self) -> None:
        self.dismiss(None)


class SearchScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    SearchScreen { align: center middle; }
    #search-box { width: 80%; height: 80%; border: round $accent; padding: 1 2; }
    #search-results { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Cerrar"),
        Binding("a", "enqueue_selected", "Encolar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._results: list[Track] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Input(placeholder="Buscar en YouTube... (Enter=buscar)", id="search-input")
            yield ListView(id="search-results")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.run_worker(self._do_search(query), exclusive=True)

    async def _do_search(self, query: str) -> None:
        results_list = self.query_one("#search-results", ListView)
        await results_list.clear()
        try:
            tracks = await asyncio.to_thread(resolver.search, query, 8)
        except Exception as exc:  # noqa: BLE001 - herramienta personal
            await results_list.append(ListItem(Label(f"Error de búsqueda: {exc}")))
            return
        self._results = tracks
        if not tracks:
            await results_list.append(ListItem(Label("Sin resultados.")))
            return
        for t in tracks:
            label = f"{t.title} ({fmt_time(t.duration)}) — {t.uploader or '?'}"
            await results_list.append(ListItem(Label(label)))
        results_list.index = 0
        results_list.focus()

    def _selected_track(self) -> Optional[Track]:
        results_list = self.query_one("#search-results", ListView)
        idx = results_list.index
        if idx is None or not (0 <= idx < len(self._results)):
            return None
        return self._results[idx]

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        track = self._selected_track()
        if track:
            self.app.engine.play_now(track)
            self.dismiss(None)

    def action_enqueue_selected(self) -> None:
        track = self._selected_track()
        if track:
            self.app.notify(f"Encolado: {track.title}")
            self.app.engine.enqueue(track)


# ---------------------------------------------------------------------------
# Paneles de las pestañas
# ---------------------------------------------------------------------------


class LibraryPane(Vertical):
    BINDINGS = [
        Binding("a", "enqueue_selected", "Encolar"),
        Binding("d", "delete_selected", "Borrar"),
        Binding("r", "rename_selected", "Renombrar"),
        Binding("escape", "unfocus_filter", "Cerrar filtro", show=False),
    ]

    DEFAULT_CSS = """
    LibraryPane > DataTable {
        scrollbar-color: $primary;
        scrollbar-color-hover: $primary-lighten-1;
        scrollbar-color-active: $primary-lighten-2;
        height: 1fr;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_signature: Optional[tuple] = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filtrar biblioteca...", id="lib-filter")
        table = DataTable(id="lib-table", cursor_type="row")
        table.add_columns("Título", "Autor", "Duración", "Reprod.")
        yield table

    def on_mount(self) -> None:
        self.refresh_table()
        self._last_signature = self._current_signature()
        self.query_one("#lib-table", DataTable).focus()

    def _current_signature(self) -> tuple:
        return tuple((e.track.id, e.play_count, e.last_played_at) for e in self.app.library.all())

    def refresh_if_changed(self) -> None:
        """Refresca la tabla SOLO si la biblioteca cambió desde el último chequeo.

        Se llama en cada tick del poll de estado (cada 0.5s) para que una
        pista reproducida desde otro lado (ej. Buscar con 'f') aparezca sin
        acción manual — pero solo reconstruye la tabla cuando algo cambió
        de verdad, preservando la fila seleccionada por id de pista (no por
        índice, ya que el orden puede cambiar al reordenar por
        last_played_at).
        """
        signature = self._current_signature()
        if signature == self._last_signature:
            return
        self._last_signature = signature

        table = self.query_one("#lib-table", DataTable)
        selected_id = None
        if table.row_count and table.cursor_row is not None:
            try:
                selected_id = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            except Exception:  # noqa: BLE001 - herramienta personal
                selected_id = None

        self.refresh_table(self.query_one("#lib-filter", Input).value)

        if selected_id is not None:
            try:
                row_index = table.get_row_index(selected_id)
                table.cursor_coordinate = Coordinate(row_index, table.cursor_coordinate.column)
            except Exception:  # noqa: BLE001 - la fila puede haber sido borrada
                pass

    def refresh_table(self, query: str = "") -> None:
        table = self.query_one("#lib-table", DataTable)
        table.clear()
        entries = self.app.library.search(query) if query else self.app.library.all()
        entries.sort(key=lambda e: e.last_played_at, reverse=True)
        for e in entries:
            table.add_row(
                e.track.title,
                e.track.uploader or "?",
                fmt_time(e.track.duration),
                str(e.play_count),
                key=e.track.id,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "lib-filter":
            self.refresh_table(event.value)

    def _selected_track(self) -> Optional[Track]:
        table = self.query_one("#lib-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        entry = self.app.library.get(row_key)
        return entry.track if entry else None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        entry = self.app.library.get(event.row_key.value)
        if entry:
            self.app.engine.play_now(entry.track)

    def action_enqueue_selected(self) -> None:
        track = self._selected_track()
        if track:
            self.app.engine.enqueue(track)
            self.app.notify(f"Encolado: {track.title}")

    def action_delete_selected(self) -> None:
        track = self._selected_track()
        if track:
            self.app.library.remove(track.id)
            self.refresh_table(self.query_one("#lib-filter", Input).value)
            self._last_signature = self._current_signature()

    def action_rename_selected(self) -> None:
        track = self._selected_track()
        if not track:
            return

        def _apply(new_title: Optional[str]) -> None:
            if new_title:
                self.app.library.rename(track.id, new_title)
                self.refresh_table(self.query_one("#lib-filter", Input).value)
                self._last_signature = self._current_signature()

        self.app.push_screen(RenameScreen(track.title), _apply)

    def action_unfocus_filter(self) -> None:
        self.query_one("#lib-table", DataTable).focus()


class QueuePane(Vertical):
    BINDINGS = [
        Binding("d", "remove_selected", "Quitar"),
        Binding("c", "clear_all", "Vaciar"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_signature: Optional[tuple] = None

    def compose(self) -> ComposeResult:
        yield ListView(id="queue-list")

    async def on_mount(self) -> None:
        await self.refresh_list()

    async def refresh_list(self) -> None:
        """Reconstruye la lista SOLO si la cola/pista actual cambió.

        Se llama en cada tick del poll de estado (cada 0.5s); si siempre
        reconstruyera incondicionalmente, perdería la posición del cursor de
        selección del usuario en cada tick (clear() resetea el índice).
        """
        queue = self.app.engine.queue
        current = self.app.engine.status.track
        signature = (tuple(t.id for t in queue), current.id if current else None)
        if signature == self._last_signature:
            return
        self._last_signature = signature

        list_view = self.query_one("#queue-list", ListView)
        preserved_index = list_view.index
        await list_view.clear()
        for t in queue:
            marker = "▶ " if current is not None and t.id == current.id else "  "
            await list_view.append(ListItem(Label(f"{marker}{t.title}")))
        if queue:
            list_view.index = min(preserved_index, len(queue) - 1) if preserved_index is not None else 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#queue-list", ListView).index
        queue = self.app.engine.queue
        if idx is not None and 0 <= idx < len(queue):
            self.app.engine.play_now(queue[idx])

    async def action_remove_selected(self) -> None:
        idx = self.query_one("#queue-list", ListView).index
        if idx is not None:
            self.app.engine.remove_from_queue(idx)
            await self.refresh_list()

    async def action_clear_all(self) -> None:
        self.app.engine.clear_queue()
        await self.refresh_list()


class HistoryPane(Vertical):
    DEFAULT_CSS = """
    HistoryPane > DataTable {
        height: 1fr;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_signature: Optional[tuple] = None

    def compose(self) -> ComposeResult:
        table = DataTable(id="hist-table", cursor_type="row")
        table.add_columns("Título", "Autor", "Reproducido")
        yield table

    def on_mount(self) -> None:
        self.refresh_table()
        self._last_signature = self._current_signature()

    def _current_signature(self) -> tuple:
        return tuple(e.played_at for e in self.app.history.recent(100))

    def refresh_if_changed(self) -> None:
        """Refresca SOLO si el historial cambió (mismo patrón que Library/Queue).

        Sin esto, `refresh_table()` solo se llamaba una vez en `on_mount` —
        si la pestaña se monta antes de que se reproduzca algo (lo más
        común, ya que TabbedContent monta sus tres pestañas al arrancar),
        el historial quedaba vacío para siempre sin importar cuánto se
        reprodujera después.
        """
        signature = self._current_signature()
        if signature == self._last_signature:
            return
        self._last_signature = signature
        self.refresh_table()

    def refresh_table(self) -> None:
        table = self.query_one("#hist-table", DataTable)
        table.clear()
        for i, e in enumerate(self.app.history.recent(100)):
            table.add_row(e.track.title, e.track.uploader or "?", e.played_at[:19], key=str(i))
        self._entries = self.app.history.recent(100)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = int(event.row_key.value)
        entries = self.app.history.recent(100)
        if 0 <= idx < len(entries):
            self.app.engine.play_now(entries[idx].track)


# ---------------------------------------------------------------------------
# App principal
# ---------------------------------------------------------------------------


class YtAudioApp(App):
    TITLE = "ytaudio"
    CSS = f"""
    Screen {{ background: {_BG_PANEL}; }}
    #now-playing-panel {{ height: auto; background: {_BG_PANEL}; padding: 0; }}
    #np-spacer {{ height: 1; }}
    NowPlayingRow, ProgressRow, TabsShortcutsRow {{ padding: 0 1; }}
    TabbedContent {{ height: 1fr; }}
    TabbedContent > ContentTabs {{ display: none; }}
    """
    BINDINGS = [
        Binding("space", "toggle_pause", "Pausa"),
        Binding("]", "next_track", "Siguiente"),
        Binding("[", "prev_track", "Anterior"),
        Binding("+", "vol_up", "Vol+"),
        Binding("=", "vol_up", "Vol+", show=False),
        Binding("-", "vol_down", "Vol-"),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("e", "cycle_repeat", "Repeat"),
        Binding("0", "restart_track", "Reiniciar"),
        Binding("f", "open_search", "Buscar"),
        Binding("1", "show_tab('lib')", "Biblioteca"),
        Binding("2", "show_tab('queue')", "Cola"),
        Binding("3", "show_tab('hist')", "Historial"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self, initial_query: Optional[str] = None, initial_volume: Optional[int] = None) -> None:
        super().__init__()
        ensure_dirs()
        self.settings = load_settings()
        if initial_volume is not None:
            self.settings.volume = max(0, min(130, initial_volume))

        self.library = Library()
        self.history = History()
        self.cache = AudioCache()
        self.engine = PlayerEngine(self.library, self.history, self.cache, self.settings)

        self._initial_query = initial_query
        self._mpris: Optional[MprisService] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="now-playing-panel"):
            yield EqualizerWidget(id="eq")
            yield Static(id="np-spacer")
            yield NowPlayingRow(id="now-playing")
            yield ProgressRow(id="progress")
            yield TabsShortcutsRow(id="tabs-shortcuts")
        with TabbedContent(initial="lib"):
            with TabPane("Biblioteca (1)", id="lib"):
                yield LibraryPane()
            with TabPane("Cola (2)", id="queue"):
                yield QueuePane()
            with TabPane("Historial (3)", id="hist"):
                yield HistoryPane()

    async def on_mount(self) -> None:
        self.set_interval(0.5, self._poll_status)
        self._mpris = MprisService(self.engine)
        await self._mpris.start()  # best-effort, nunca lanza
        self.engine.add_level_listener(self._on_audio_levels)

        self.query_one(NowPlayingRow).show_idle("Nada reproduciéndose todavía. Presioná 'f' para buscar.")
        self.query_one(TabsShortcutsRow).update_active("lib")

        if self._initial_query:
            self.run_worker(self._play_initial_query(self._initial_query), exclusive=True)

    def _on_audio_levels(self, levels: list) -> None:
        # Se llama desde el hilo lector de audiocap.py, no desde el loop de
        # Textual — hay que marshalear con call_from_thread antes de tocar
        # cualquier widget (mismo patrón que usa mpris.py con su propio loop).
        try:
            self.call_from_thread(self._apply_audio_levels, levels)
        except Exception:  # noqa: BLE001 - la app puede estar cerrándose
            pass

    def _apply_audio_levels(self, levels: list) -> None:
        try:
            self.query_one("#eq", EqualizerWidget).set_levels(levels)
        except Exception:  # noqa: BLE001 - el widget puede no estar montado aún
            pass

    async def _play_initial_query(self, query: str) -> None:
        self.query_one(NowPlayingRow).show_idle(f"Resolviendo: {query}...")
        try:
            track = await asyncio.to_thread(resolver.resolve_single, query)
        except Exception as exc:  # noqa: BLE001 - herramienta personal
            self.query_one(NowPlayingRow).show_idle(f"Error al resolver '{query}': {exc}")
            return
        self.engine.play_now(track)

    async def _poll_status(self) -> None:
        status = self.engine.status
        eq = self.query_one("#eq", EqualizerWidget)
        eq.playing = status.state == PlaybackState.PLAYING
        eq.volume = status.volume
        eq.position = status.position
        eq.duration = status.duration

        now_playing = self.query_one(NowPlayingRow)
        if status.track is None:
            now_playing.show_idle("Nada reproduciéndose todavía. Presioná 'f' para buscar.")
        else:
            now_playing.show_status(status)

        progress_row = self.query_one(ProgressRow)
        progress_row.position = status.position
        progress_row.duration = status.duration

        try:
            await self.query_one(QueuePane).refresh_list()
        except Exception:  # noqa: BLE001 - el panel puede no estar montado aún
            pass

        try:
            self.query_one(LibraryPane).refresh_if_changed()
        except Exception:  # noqa: BLE001 - el panel puede no estar montado aún
            pass

        try:
            self.query_one(HistoryPane).refresh_if_changed()
        except Exception:  # noqa: BLE001 - el panel puede no estar montado aún
            pass

    # -- acciones globales --------------------------------------------------

    def action_toggle_pause(self) -> None:
        self.engine.toggle_pause()

    def action_next_track(self) -> None:
        self.engine.next()

    def action_prev_track(self) -> None:
        self.engine.previous()

    def action_vol_up(self) -> None:
        self.engine.set_volume(self.engine.status.volume + 5)

    def action_vol_down(self) -> None:
        self.engine.set_volume(self.engine.status.volume - 5)

    def action_toggle_shuffle(self) -> None:
        self.engine.set_shuffle(not self.engine.status.shuffle)

    def action_cycle_repeat(self) -> None:
        order = [RepeatMode.OFF, RepeatMode.ONE, RepeatMode.ALL]
        current = self.engine.status.repeat
        nxt = order[(order.index(current) + 1) % len(order)]
        self.engine.set_repeat(nxt)

    def action_restart_track(self) -> None:
        self.engine.restart_current()

    def action_open_search(self) -> None:
        self.push_screen(SearchScreen())

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id
        self.query_one(TabsShortcutsRow).update_active(tab_id)
        focus_target = {"lib": "#lib-table", "queue": "#queue-list", "hist": "#hist-table"}.get(tab_id)
        if focus_target:
            try:
                self.query_one(focus_target).focus()
            except Exception:  # noqa: BLE001 - el panel puede no estar montado aún
                pass

    async def action_quit(self) -> None:
        status = self.engine.status
        self.settings.volume = status.volume
        self.settings.shuffle = status.shuffle
        self.settings.repeat = status.repeat
        try:
            save_settings(self.settings)
        except Exception:  # noqa: BLE001 - herramienta personal
            pass
        try:
            self.engine.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if self._mpris is not None:
            try:
                await self._mpris.stop()
            except Exception:  # noqa: BLE001
                pass
        self.exit()
