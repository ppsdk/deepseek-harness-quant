"""Optional raw-price cash distributions, share splits and personal-stock FIFO tax."""
from datetime import date
import math


class CashActions:
    def __init__(self, panels):
        self.events = panels.get('_cash_actions', [])
        if self.events and panels.get('_execution_price_basis') != 'raw':
            raise ValueError('现金分红/拆分只能配合明确声明的未复权成交价格，禁止重复计算分红。')
        self.lots, self.entitlements, self.processed = {}, {}, set()
        self.receivable, self.gross, self.tax = 0., 0., 0.
        self.ledger = []
        ids = set()
        for e in self.events:
            if e['id'] in ids or e['code'] not in panels['close'].columns:
                raise ValueError('公司行为编号重复或证券不在行情中。')
            ids.add(e['id'])
            for key in ('record_date','ex_date','pay_date','available_date'):
                if date.fromisoformat(e[key]).isoformat()!=e[key]:
                    raise ValueError('公司行为日期无效。')
            if not e['available_date']<=e['record_date']<e['ex_date']<=e['pay_date']:
                raise ValueError('公司行为可用/登记/除权/到账日期顺序无效。')
            if (not math.isfinite(e['cash_per_share']) or e['cash_per_share']<0 or
                    not math.isfinite(e['split_ratio']) or e['split_ratio']<=0 or
                    e['tax_kind'] not in ('personal_stock','fund_exempt')):
                raise ValueError('公司行为金额、拆分比例或税种无效。')
        self.events = sorted(self.events,key=lambda e:(e['record_date'],e['id']))

    def buy(self, code, qty, day):
        if self.events:
            self.lots.setdefault(code,[]).append(dict(quantity=qty,acquired=day,dividend_per_share=0.))

    @staticmethod
    def rate(acquired, sold):
        bought=date.fromisoformat(acquired);end=date.fromisoformat(sold)
        # Calendar anniversaries rather than 30/365-day approximations.
        import calendar
        month=bought.month%12+1;year=bought.year+(bought.month==12)
        one_month=date(year,month,min(bought.day,calendar.monthrange(year,month)[1]))
        one_year=date(bought.year+1,bought.month,min(bought.day,calendar.monthrange(bought.year+1,bought.month)[1]))
        return .2 if end<=one_month else .1 if end<=one_year else 0.

    def sell(self, code, qty, day):
        if not self.events:
            return 0.
        remaining=qty; tax=0.
        for lot in self.lots.get(code,[]):
            sold=min(remaining,lot['quantity'])
            tax+=sold*lot['dividend_per_share']*self.rate(lot['acquired'],day)
            lot['quantity']-=sold;remaining-=sold
            if remaining<=1e-8:break
        if remaining>1e-6:
            raise ValueError('公司行为FIFO份额与成交账本不一致。')
        self.lots[code]=[lot for lot in self.lots.get(code,[]) if lot['quantity']>1e-8]
        if tax:
            self.tax+=tax
            self.ledger.append(dict(date=day,code=code,type='dividend_tax_on_sale',amount=tax))
        return tax

    def record(self, event, day):
        key=(event['id'],'record')
        if key in self.processed:return
        amount=0.;qty=0.
        for lot in self.lots.get(event['code'],[]):
            qty+=lot['quantity'];amount+=lot['quantity']*event['cash_per_share']
            if event['tax_kind']=='personal_stock':
                lot['dividend_per_share']+=event['cash_per_share']
        self.entitlements[event['id']]=amount
        self.processed.add(key)
        self.ledger.append(dict(date=day,code=event['code'],event_id=event['id'],type='entitlement',quantity=qty,amount=amount))

    def start_day(self, day, shares, basis, peaks, marks, plan):
        cash=0.
        # If a record date has no quote, no transaction occurred on that date;
        # the preceding observed holdings still determine the entitlement.
        timeline=[]
        for e in self.events:
            for kind,key in [('record','record_date'),('ex','ex_date'),('pay','pay_date')]:
                if (e['id'],kind) not in self.processed and (e[key]<day if kind=='record' else e[key]<=day):
                    timeline.append((e[key],{'record':0,'ex':1,'pay':2}[kind],kind,e))
        for when,_,kind,e in sorted(timeline,key=lambda x:(x[0],x[1],x[3]['id'])):
            key=(e['id'],kind)
            if key in self.processed:continue
            code=e['code']
            if kind=='record':
                self.record(e,when);continue
            if (e['id'],'record') not in self.processed:
                self.record(e,e['record_date'])
            if kind=='ex':
                amount=self.entitlements[e['id']]
                self.receivable+=amount
                ratio=e['split_ratio']
                if ratio!=1 and code in shares:
                    shares[code]*=ratio
                    for target in (basis,peaks,marks):
                        if code in target:target[code]/=ratio
                    for lot in self.lots.get(code,[]):
                        lot['quantity']*=ratio;lot['dividend_per_share']/=ratio
                    if plan is not None and code in plan['quantities']:
                        plan['quantities'][code]*=ratio
                    self.ledger.append(dict(date=when,code=code,event_id=e['id'],type='split',ratio=ratio))
            elif kind=='pay':
                amount=self.entitlements[e['id']]
                self.receivable-=amount;cash+=amount;self.gross+=amount
                self.ledger.append(dict(date=when,code=code,event_id=e['id'],type='cash_dividend',amount=amount))
            self.processed.add(key)
        return cash

    def end_day(self, day):
        for e in self.events:
            if e['record_date']==day:self.record(e,day)

    def report(self, final_day):
        conditional=sum(lot['quantity']*lot['dividend_per_share']*self.rate(lot['acquired'],final_day)
                        for lots in self.lots.values() for lot in lots)
        return dict(gross_dividends=self.gross,dividend_tax_paid=self.tax,net_dividends=self.gross-self.tax,
                    receivable=self.receivable,conditional_tax_if_sold=conditional,events=self.ledger,
                    price_basis='raw' if self.events else 'unchanged',tax_method='personal_stock_FIFO_calendar_holding_period')
