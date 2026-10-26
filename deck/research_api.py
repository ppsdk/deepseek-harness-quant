"""Research studio routes. All changing lists come from configuration or local stores."""
from pathlib import Path


def get(path,query):
    from factors.research_library import read_config
    from factors.research_manager import revision,experiment_list,experiment_detail,analysis_records
    from data.research_services import stock_list,paper_list,timing_status
    from backtest.research_ml import ml_config
    from backtest.research_jobs import status
    from backtest.research_pools import list_pools
    if path=='/api/studio/pools':return list_pools(query)
    if path=='/api/studio/pools/screen':
        from backtest.research_screener import screen
        return screen(query)
    if path=='/api/studio/signals':
        from backtest.research_signals import plans
        return plans(query)
    if path=='/api/studio/signals/chart':
        from backtest.research_signals import signal_chart
        return signal_chart(query)
    if path=='/api/studio/data-quality':
        from data.research_quality import latest
        return latest()
    if path=='/api/studio/positions':
        from backtest.research_positions import overview
        return overview(query)
    if path=='/api/studio/dashboard':
        from data.market_dashboard import dashboard
        return dashboard(query)
    if path=='/api/studio/automation':
        from backtest.research_automation import overview
        return overview(query)
    if path=='/api/studio/stock/profile':
        from data.stock_profile import profile
        return profile(query)
    if path=='/api/studio/stock/ai':
        from data.stock_profile import ai_status
        return ai_status(query)
    if path=='/api/studio/market/analysis':
        from data.market_analysis import analysis
        return analysis(query)
    if path=='/api/studio/dual':
        from backtest.dual_momentum import overview
        return overview(query)
    if path=='/api/studio/dual/detail':
        from backtest.dual_momentum import detail
        return detail(query)
    if path=='/api/studio/assistant/context':
        from backtest.research_assistant import context
        return context(query)
    if path=='/api/studio/market':
        from data.research_charts import market
        return market(query)
    if path=='/api/studio/config':
        return dict(ok=True,**read_config('research_services'),workflow=read_config('research_workflow'),factor_revision=revision(),ml=ml_config(),references=read_config('research_references'))
    if path=='/api/studio/stocks':return stock_list(query)
    if path=='/api/studio/papers':return paper_list()
    if path=='/api/studio/timing':return timing_status(query)
    if path=='/api/studio/catalog':
        from backtest.research_catalog import catalog
        return catalog()
    if path=='/api/studio/experiments':return experiment_detail(query['id']) if query.get('id') else experiment_list(query)
    if path=='/api/studio/analyses':return analysis_records(query)
    if path.startswith('/api/studio/jobs/'):
        bits=path.split('/');return status(bits[4],with_result=len(bits)>5 and bits[5]=='result')
    raise ValueError('研究接口不存在。')


def post(path,payload,compute_lock):
    from factors.research_manager import save_factor,annotate
    from backtest.research_jobs import start
    if path=='/api/studio/signals':
        from backtest.research_signals import save
        return save(payload)
    if path=='/api/studio/formulas/build':
        from factors.research_formula import build_conditions
        return build_conditions(payload)
    if path=='/api/studio/formulas/validate':
        from factors.research_formula import compile_formula
        return dict(ok=True,spec=compile_formula(payload.get('formula','')),message='公式与依赖检查通过。可继续用本地数据预览。')
    if path=='/api/studio/positions/plan':
        from backtest.research_positions import plan
        return plan(payload)
    if path=='/api/studio/dashboard/refresh':
        from data.market_dashboard import refresh
        return refresh()
    if path=='/api/studio/dashboard/history/import':
        from data.market_interpretation import import_dividends
        return import_dividends(payload)
    if path=='/api/studio/assistant/draft':
        from backtest.research_assistant import draft
        return draft(payload)
    if path=='/api/studio/assistant/send':
        from backtest.research_assistant import draft
        return draft({**payload,'send':True})
    if path=='/api/studio/stock/ai':
        from data.stock_profile import ai_analyse
        return ai_analyse(payload)
    if path=='/api/studio/market/analysis':
        from data.market_analysis import build
        return build(payload,compute_lock)
    if path=='/api/studio/dual/ai':
        from backtest.dual_momentum import ai_review
        return ai_review(payload)
    if path=='/api/studio/automation/tasks':
        from backtest.research_automation import save_task
        return save_task(payload)
    if path=='/api/studio/automation/delete':
        from backtest.research_automation import delete_task
        return delete_task(payload)
    if path=='/api/studio/automation/run':
        from backtest.research_automation import run_now
        return run_now(payload,compute_lock)
    if path=='/api/studio/pools':
        from backtest.research_pools import save_pool
        return save_pool(payload)
    if path=='/api/studio/factors':return save_factor(payload)
    if path=='/api/studio/experiments':return annotate(payload)
    if path=='/api/studio/jobs':return start(payload,compute_lock)
    raise ValueError('研究接口不存在。')
