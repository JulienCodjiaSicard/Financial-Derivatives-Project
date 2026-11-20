"""ECE — Partie 3, composante EUR/USD (sans les risques de taux, crédit ni call).

Sources : CSV officiel ECB téléchargé et tableau ATM/RR fourni par Saxo
(spot coté 1,0992). Ce programme ne prétend pas reconstruire
un smile complet sans Butterfly, ni calibrer les échéances > 1 an.

Exécution : python pricing_fx.py
Dépendances : python -m pip install pandas numpy scipy matplotlib
"""

import csv
import math
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
ECB = ROOT / 'eurusd_bce_2026-05-06_2026-11-06.csv'
OUT = ROOT / 'resultats_fx'
OUT.mkdir(exist_ok=True)
START, END, VAL_DATE = date(2026,5,6), date(2026,11,6), date(2026,11,17)
NOMINAL, COUPON = 50_000_000, .07
R_EUR, R_USD = .03, .03  # hypotheses uniquement, pas donnees observees
TENORS = [(7/365,'1 semaine',5.67,-.48),(1/12,'1 mois',6.24,-.52),(.25,'3 mois',6.13,-.59),(.5,'6 mois',6.61,-.78),(.75,'9 mois',6.69,-.80),(1.,'1 an',6.76,-.84)]

def cdf(x): return .5 * math.erfc(-x / math.sqrt(2))
def probability(spot, barrier, t, vol):
    if t<=0: return float(spot<barrier)
    if vol<=0: return float(spot * math.exp((R_USD-R_EUR)*t)<barrier)
    d1=(math.log(spot/barrier)+(R_USD-R_EUR+.5*vol**2)*t)/(vol*math.sqrt(t))
    return cdf(-d1)  # digital coupon en EUR sous mesure risque-neutre EUR

def price(spot, barrier, t, vol):
    return NOMINAL*COUPON*math.exp(-R_EUR*t)*probability(spot,barrier,t,vol)

def save_csv(name,headers,rows):
    with (OUT/name).open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(headers);writer.writerows(rows)

def svg_line(name,title,xs,series,ylab,labels=None):
    W,H=1000,500;left,right,top,bottom=85,35,65,75
    xlo,xhi=min(xs),max(xs)
    if xhi==xlo:xhi=xlo+1
    vals=[v for _,ys,_ in series for v in ys]
    lo,hi=min(vals),max(vals);pad=max((hi-lo)*.12,.001);lo-=pad;hi+=pad
    X=lambda a:left+(a-xlo)/(xhi-xlo)*(W-left-right)
    Y=lambda b:top+(hi-b)/(hi-lo)*(H-top-bottom)
    colors=['#2563eb','#e11d48','#059669','#a855f7']
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="100%" height="100%" fill="white"/>',f'<text x="{W/2}" y="33" font-family="Arial" font-size="21" text-anchor="middle">{escape(title)}</text>']
    for k in range(6):
        v=lo+(hi-lo)*k/5;y=Y(v)
        parts += [f'<line x1="{left}" y1="{y:.1f}" x2="{W-right}" y2="{y:.1f}" stroke="#e5e7eb"/>',f'<text x="{left-8}" y="{y+4:.1f}" font-size="12" text-anchor="end" font-family="Arial">{v:.3f}</text>']
    for i,(name1,ys,dashed) in enumerate(series):
        points=' '.join(f'{X(x):.1f},{Y(y):.1f}' for x,y in zip(xs,ys))
        dash='stroke-dasharray="9 6"' if dashed else ''
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[i]}" stroke-width="2.5" {dash}/>')
        parts.append(f'<line x1="{left+i*230}" y1="{H-24}" x2="{left+25+i*230}" y2="{H-24}" stroke="{colors[i]}" stroke-width="3" {dash}/><text x="{left+32+i*230}" y="{H-19}" font-size="13" font-family="Arial">{escape(name1)}</text>')
    for i in range(6):
        x=xlo+(xhi-xlo)*i/5
        tick=labels[i] if labels and len(labels)==6 else f'{x:.2f}'
        parts.append(f'<text x="{X(x):.1f}" y="{H-bottom+23}" text-anchor="middle" font-size="12" font-family="Arial">{escape(str(tick))}</text>')
    parts.append(f'<text x="25" y="{H/2}" text-anchor="middle" transform="rotate(-90,25,{H/2})" font-family="Arial" font-size="14">{escape(ylab)}</text></svg>')
    (OUT/name).write_text(''.join(parts),encoding='utf-8')

def main():
    if not ECB.exists():
        raise SystemExit('CSV BCE introuvable. Mets "eurusd_bce_2026-05-06_2026-11-06.csv" dans le meme dossier que ce script.')
    with ECB.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        data = []

        for row in reader:
            try:
                d = date.fromisoformat(row['date'])
                x = float(row['eurusd'])

                if math.isfinite(x):
                    data.append((d, x))

            except (KeyError, ValueError, TypeError):
                continue
    hist=sorted((d,x) for d,x in data if START<=d<=END)
    if not hist:raise SystemExit('Aucune donnee BCE sur la periode de la barriere')
    peak=max(hist,key=lambda item:item[1]);barrier=peak[1]
    recent=[(d,x) for d,x in data if d<=VAL_DATE]
    spot_date,spot=max(recent,key=lambda item:item[0])
    print(f'Barriere : {barrier:.4f}, date du pic : {peak[0]}, observations : {len(hist)}')
    print(f'Spot BCE a la date {spot_date}: {spot:.4f}')
    print('ATTENTION : ATM/RR Saxo de la consultation, taux EUR/USD 3% supposes, pas de BF ni de smile calibre.')
    save_csv('01_historique_BCE.csv',['date','eurusd'],[(d.isoformat(),x) for d,x in hist])
    save_csv('02_cotations_Saxo.csv',['maturite_annees','maturite','ATM_vol_pct','RR25_points_de_vol'],TENORS)
    # Chart historic: use sampled dates, barrier as constant series
    xs=list(range(len(hist)));values=[x for _,x in hist]
    svg_line('01_barriere_eurusd.svg','EUR/USD BCE — barriere contractuelle',xs,[('EUR/USD',values,False),('Barriere', [barrier]*len(hist),True)],'USD pour 1 EUR')
    ts=[t for t,_,_,_ in TENORS]
    svg_line('02_volatilite_ATM.svg','EUR/USD — volatilite ATM Saxo',ts,[('ATM (%)',[v for _,_,v,_ in TENORS],False)],'Volatilite (%)')
    svg_line('03_risk_reversal.svg','EUR/USD — Risk Reversal 25 delta Saxo',ts,[('RR 25Δ (points)',[v for _,_,_,v in TENORS],False)],'Points de volatilite')
    rows=[]
    for t,lab,atm,rr in TENORS:
        if t not in (.5,1.):continue
        v=atm/100;p=probability(spot,barrier,t,v);pv=price(spot,barrier,t,v)
        h=.0001;vs=.01
        delta=(price(spot+h,barrier,t,v)-price(spot-h,barrier,t,v))/(2*h)
        gamma=(price(spot+h,barrier,t,v)-2*pv+price(spot-h,barrier,t,v))/(h*h)
        vega_point=(price(spot,barrier,t,v+vs)-price(spot,barrier,t,v-vs))/2
        rows.append([lab,t,spot,barrier,atm,rr,p,pv,delta,gamma,vega_point])
        print(f'{lab}: P(FX < B) = {p:.2%}; coupon FX actualise = {pv:,.2f} EUR; Delta = {delta:,.0f}; variation approx pour +1 pt vol = {vega_point:,.0f} EUR')
    save_csv('03_pricing_fx_et_greeks.csv',['maturite','t_annees','spot_BCE','barriere','atm_pct','rr25_points','probabilite_EUR_neutre','valeur_coupon_EUR','delta_EUR_par_unite_FX','gamma_EUR_par_FX_carre','vega_1pt_EUR_approx'],rows)
    sig=[.035+i*(.12-.035)/79 for i in range(80)]
    svg_line('04_sensibilite_volatilite.svg','Valeur du coupon FX vs volatilite — hypotheses de taux', [s*100 for s in sig], [('6 mois',[price(spot,barrier,.5,s)/1e6 for s in sig],False),('1 an',[price(spot,barrier,1.,s)/1e6 for s in sig],False)],'Valeur du coupon (millions EUR)')
    (OUT/'A_LIRE.txt').write_text('Donnees observees : CSV BCE et tableau ATM / RR Saxo fourni dans la conversation.\n'
      'Hypotheses : 3% EUR et 3% USD, nominal 50 M EUR, coupon 7% semestriel.\n'
      'Spot BCE au 17 novembre alors que la barriere est historique au 6 novembre.\n'
      'Sans BF et volatilites au-dela de 1 an, on ne peut pas calibrer un smile complet ni les 14 coupons.\n'
      'Pricing digital a volatilite ATM constante : approximation, ne valorise pas taux, credit ou callable.\n',encoding='utf-8')
    print('Resultats dans :',OUT)

if __name__=='__main__':main()
