"""
Qt / Matplotlib bridge helpers.

These utilities embed existing Matplotlib-based tab renderers inside Qt
widgets, without changing the underlying plotting code.
"""

from typing import Callable, Any, Tuple

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas


def create_figure(figsize: Tuple[float, float] = (8.0, 4.5)) -> Figure:
    """
    Create a Matplotlib Figure suitable for embedding in Qt.

    Tabs are responsible for adding Axes and calling their renderers.
    """
    fig = Figure(figsize=figsize, facecolor="#0c0e0c")
    return fig


def create_canvas(fig: Figure) -> FigureCanvas:
    """
    Wrap a Matplotlib Figure in a Qt-compatible canvas.
    """
    return FigureCanvas(fig)


def render_into_single_axes(
    fig: Figure,
    render_fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Convenience to:
    - create a single Axes on the figure
    - call the existing render(ax, ...) function.

    This mirrors how most tab renderers are used in ui_layout.
    """
    ax = fig.add_subplot(1, 1, 1)
    render_fn(ax, *args, **kwargs)

