"""Causal moving-average conditions for low-frequency event trading."""
def attach(panels,p):
    if p.get('trade_mode','fixed')!='event':return
    close=panels['close'];fast=close.rolling(p['entry_ma_fast']).mean();slow=close.rolling(p['entry_ma_slow']).mean()
    exit_ma=close.rolling(p['exit_ma_period']).mean();n=p['ma_confirm_days']
    known=fast.notna()&slow.notna()&exit_ma.notna()&close.notna()
    enter=(close.gt(fast)&fast.gt(slow)).rolling(n).sum().eq(n).astype(float).where(known)
    leave=close.lt(exit_ma).rolling(n).sum().eq(n).astype(float).where(known)
    if '_entry' in panels:enter=enter.where(panels['_entry'].gt(0),0.).where(panels['_entry'].notna()&known)
    if '_exit' in panels:leave=leave.where(~panels['_exit'].gt(0),1.).where(panels['_exit'].notna()&known)
    panels['_entry'],panels['_exit']=enter,leave
