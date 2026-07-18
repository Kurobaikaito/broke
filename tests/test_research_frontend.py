import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ResearchFrontendContractTestCase(unittest.TestCase):
    def test_research_page_exposes_run_status_and_stop_controls(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        research_markup = html[html.index('id="researchView"'):html.index('id="dataView"')]
        for element_id in (
            "startResearchBtn",
            "forceFullResearchBtn",
            "stopResearchBtn",
            "researchStatusBadge",
            "researchRunModeText",
            "researchCurrentStepText",
            "researchStepProgress",
            "researchWindowProgress",
            "researchProgressBar",
            "researchLogs",
        ):
            self.assertIn(f'id="{element_id}"', research_markup)
        self.assertIn('fetchJson(`/api/research/run?', javascript)
        self.assertIn('fetchJson("/api/research/status")', javascript)
        self.assertIn('fetchJson("/api/research/stop"', javascript)

    def test_research_start_controls_request_auto_or_full_mode(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        research_markup = html[html.index('id="researchView"'):html.index('id="dataView"')]

        self.assertIn(
            'id="startResearchBtn" class="primary-btn" type="button">自动更新并发布',
            research_markup,
        )
        self.assertIn(
            'id="forceFullResearchBtn" class="secondary-btn" type="button">强制完整回测',
            research_markup,
        )
        self.assertIn(
            'new URLSearchParams({ capital: String(state.researchCapital), mode })',
            javascript,
        )
        self.assertIn(
            '$("startResearchBtn").addEventListener("click", () => startResearch("auto"))',
            javascript,
        )
        self.assertIn(
            '$("forceFullResearchBtn").addEventListener("click", () => startResearch("full"))',
            javascript,
        )

    def test_research_auto_mode_is_explained_and_actual_mode_is_rendered(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        research_markup = html[html.index('id="researchView"'):html.index('id="dataView"')]

        self.assertIn("首次执行完整回测，后续自动选择增量更新", research_markup)
        self.assertIn("首次执行或配置变化时完整回测", research_markup)
        self.assertIn("配置一致时只训练最新窗口", research_markup)
        self.assertIn("每个周期验证成功后安全切换对应推荐", research_markup)
        self.assertIn("失败不会污染旧快照", research_markup)
        self.assertNotIn("所有周期成功后才发布", research_markup)
        self.assertNotIn("三个预测周期全部成功后原子更新推荐", research_markup)
        self.assertIn('latest: "增量更新"', javascript)
        self.assertIn('full: "完整回测"', javascript)
        self.assertIn("payload.actual_run_mode || payload.run_mode || \"auto\"", javascript)
        self.assertIn('$("researchRunModeText").textContent', javascript)

    def test_research_progress_renders_pipeline_steps_and_rolling_windows(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        research_markup = html[html.index('id="researchView"'):html.index('id="dataView"')]

        self.assertIn(
            '当前步骤 <strong id="researchCurrentStepText">--</strong> · '
            '<strong id="researchStepProgress">0 / 0</strong>',
            research_markup,
        )
        self.assertIn(
            '滚动窗口 <strong id="researchWindowProgress">0 / 0</strong>',
            research_markup,
        )
        self.assertIn(
            '$("researchCurrentStepText").textContent = payload.current_step || "--"',
            javascript,
        )
        self.assertIn(
            '`${payload.step_completed ?? 0} / ${payload.step_total ?? 0}`',
            javascript,
        )
        self.assertIn(
            '`${payload.completed_windows ?? payload.fitted_windows ?? 0} / '
            '${payload.total_windows ?? 0}`',
            javascript,
        )

    def test_research_capital_is_independent_from_portfolio_capital(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        self.assertIn('id="researchCapitalInput"', html)
        self.assertIn("researchCapital: 50000", javascript)
        self.assertIn("String(state.researchCapital)", javascript)

    def test_sync_and_research_start_buttons_are_mutually_exclusive(self):
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        self.assertIn('$("startSyncBtn").disabled = syncRunning || researchRunning', javascript)
        self.assertIn(
            '$("startResearchBtn").disabled = researchRunning || syncRunning || state.demoMode === true',
            javascript,
        )
        self.assertIn(
            '$("forceFullResearchBtn").disabled = $("startResearchBtn").disabled',
            javascript,
        )

    def test_inventory_displays_category_and_retention_policy(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        self.assertIn("<th>分类</th>", html)
        self.assertIn("<th>保留</th>", html)
        self.assertIn("item.retention_days", javascript)


if __name__ == "__main__":
    unittest.main()
