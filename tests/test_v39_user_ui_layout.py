from app.copilot_ui import COPILOT_HTML


def test_history_items_clip_long_content_inside_sidebar() -> None:
    assert "overflow-y:auto; overflow-x:hidden" in COPILOT_HTML
    assert ".history-item { display:block; width:100%; min-width:0; max-width:100%" in COPILOT_HTML
    assert "text-overflow:ellipsis" in COPILOT_HTML


def test_desktop_history_sidebar_can_collapse_expand_and_persist() -> None:
    assert 'id="sidebarToggle"' in COPILOT_HTML
    assert '<div class="pane-actions"><button class="sidebar-toggle" id="sidebarToggle"' in COPILOT_HTML
    assert "grid-template-columns:48px minmax(550px,650px) minmax(540px,1fr)" in COPILOT_HTML
    assert "body.history-collapsed .history { padding:14px 6px; overflow:hidden; }" in COPILOT_HTML
    assert "setSidebarCollapsed" in COPILOT_HTML
    assert "ecompilot-history-collapsed" in COPILOT_HTML
    assert "aria-expanded" in COPILOT_HTML


def test_business_tabs_have_spacing_below_metrics() -> None:
    assert ".tabs { display:flex; border-bottom:1px solid var(--line); padding:0 18px" in COPILOT_HTML
    assert ".tab { min-height:56px" in COPILOT_HTML
