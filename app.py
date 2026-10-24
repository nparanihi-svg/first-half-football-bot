import os
import sqlite3
from datetime import datetime, timezone
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from engine import Snapshot,predict,edge
from provider import FootballAPI,parse_fixture
from storage import connect,record_signal
from alerts import send_telegram

load_dotenv()
st.set_page_config(page_title='First Half Signal Lab', layout='wide')
st.title('⚽ First Half Signal Lab')
st.caption('Research/paper-trading prototype — estimates are UNCALIBRATED and must not be treated as proven betting probabilities.')
key=os.getenv('API_FOOTBALL_KEY','')
mode=st.sidebar.selectbox('Data source',['Demo','API-Football live','API-Football pre-match'])
goal_rate=st.sidebar.number_input('Assumed 1H goal mean (baseline)',.1,5.,1.15,.05)
corner_rate=st.sidebar.number_input('Assumed 1H corner mean (baseline)',.1,12.,4.5,.1)
st.sidebar.caption('These are assumptions, not fitted historical team-specific rates. Historical estimation is next development stage.')
min_prob=st.sidebar.slider('Minimum estimated probability',0.,1.,.65,.01)
min_edge=st.sidebar.slider('Minimum theoretical expected return',-.5,1.,.05,.01)
show_all=st.sidebar.checkbox('Show all model estimates',True)
notifications=st.sidebar.checkbox('Enable Telegram alert sending',False)
watch_only=st.sidebar.checkbox('Only alert if verified odds were entered',True)
fixture_limit=st.sidebar.slider('Fixtures to examine',1,40,12)
st.sidebar.caption('Refreshing this page runs API calls. Avoid rapid refreshes and check quota.')

now=datetime.now(timezone.utc).isoformat()
err=[]
snapshots=[]
if mode=='Demo':
    snapshots=[Snapshot(101,'Demo League','Home FC','Away FC',18,'1H',0,1,total_shots=9,pre_goal_rate=goal_rate,pre_corner_rate=corner_rate),
               Snapshot(102,'Demo League','East United','West City',27,'1H',0,2,total_shots=6,pre_goal_rate=goal_rate,pre_corner_rate=corner_rate),
               Snapshot(103,'Demo League','Northern','Southern',0,'NS',0,0,pre_goal_rate=goal_rate,pre_corner_rate=corner_rate)]
else:
    if not key:
        st.error('Set API_FOOTBALL_KEY in .env to use the provider.'); st.stop()
    api=FootballAPI(key)
    try:
        if mode=='API-Football live':
            matches=[x for x in api.live() if x.get('fixture',{}).get('status',{}).get('short')=='1H'][:fixture_limit]
            for fixture in matches:
                try:
                    stats=api.first_half_stats(fixture['fixture']['id'])
                    snapshots.append(parse_fixture(fixture,stats,goal_rate,corner_rate))
                except Exception as e:
                    err.append(f"Fixture {fixture.get('fixture',{}).get('id')}: {e}")
        else:
            day=st.sidebar.date_input('Fixture date').isoformat()
            matches=api.upcoming(day)[:fixture_limit]
            for fixture in matches:
                s=parse_fixture(fixture,None,goal_rate,corner_rate)
                # No corners counted before kickoff; separate from missing live statistics.
                s.half_corners=0
                snapshots.append(s)
    except Exception as e: st.error(f'Provider call failed: {e}')
for e in err: st.warning(e)

st.info('Live odds are intentionally not fabricated or auto-matched. Enter verified, current decimal odds below to calculate a hypothetical edge. Disabled alerts by default.')
with st.expander('Odds inputs (optional, current verified decimal odds)',expanded=False):
    odds_text=st.text_area('CSV rows: fixture_id,market,decimal_odds — market = FH_OVER_0_5_GOALS or FH_OVER_2_5_CORNERS',
     value='',height=95,placeholder='101,FH_OVER_0_5_GOALS,1.85\n101,FH_OVER_2_5_CORNERS,1.70')
odds_map={}
for line in odds_text.splitlines():
    try:
        fid,market,odd=line.strip().split(',')
        value=float(odd)
        if value>1.0 and market in ('FH_OVER_0_5_GOALS','FH_OVER_2_5_CORNERS'):
            odds_map[(int(fid),market)]=value
    except ValueError:
        st.warning(f'Ignored invalid odds row: {line}')
rows=[]
for snap in snapshots:
    for pred in predict(snap):
        odds=odds_map.get((snap.fixture_id,pred['market']))
        v=edge(pred['probability'],odds)
        eligible=(pred['probability']>=min_prob and v is not None and v['expected_return']>=min_edge)
        rows.append({'Fixture ID':snap.fixture_id,'Match':f'{snap.home} vs {snap.away}',
                     'League':snap.league,'Minute':snap.elapsed,'Market':pred['label'],
                     'Market ID':pred['market'],'Current goals':snap.half_goals,
                     'Current corners':snap.half_corners,'Needed':pred['needed'],
                     'Probability':pred['probability'],'Odds':odds,
                     'Implied':v['implied'] if v else None,
                     'Theoretical EV':v['expected_return'] if v else None,
                     'Qualified (research only)':eligible,'Source':mode})
if not rows:
    st.warning('No eligible first-half markets with usable data at the moment.')
else:
    table=pd.DataFrame(rows).sort_values('Probability',ascending=False)
    shown=table if show_all else table[table['Qualified (research only)']]
    st.dataframe(shown.drop(columns=['Market ID','Source']),hide_index=True,use_container_width=True,
                 column_config={'Probability':st.column_config.ProgressColumn(format='%.1f',min_value=0,max_value=1),
                               'Implied':st.column_config.NumberColumn(format='%.1f'),
                               'Theoretical EV':st.column_config.NumberColumn(format='%.2f')})
    st.caption('Probabilities displayed as fractions in progress columns; e.g. 0.70 = 70%. Theoretical EV 0.05 = +5%.')
    conn=connect()
    for row in rows:
        added=record_signal(conn,(now,row['Fixture ID'],row['Match'],row['Market ID'],row['Minute'],
                        row['Probability'],row['Odds'],row['Theoretical EV'],mode,'baseline-v0.1'))
        if notifications and added and row['Qualified (research only)'] and (row['Odds'] is not None or not watch_only) and mode!='Demo':
            token=os.getenv('TELEGRAM_BOT_TOKEN',''); chat=os.getenv('TELEGRAM_CHAT_ID','')
            if not token or not chat: st.warning('Missing Telegram credentials in .env')
            else:
                try:
                    msg=(f'RESEARCH SIGNAL (uncalibrated)\n{row["Match"]} | {row["Minute"]} min\n'
                       f'{row["Market"]}\nModel {row["Probability"]:.1%} | Odds {row["Odds"]}\n'
                       f'Theoretical EV {row["Theoretical EV"]:.1%}\nVerify market, freshness and availability manually.')
                    send_telegram(token,chat,msg)
                    conn.execute('UPDATE signals SET alerted=1 WHERE fixture_id=? AND market=? AND minute=? AND data_source=?',
                                 (row['Fixture ID'],row['Market ID'],row['Minute'],mode));conn.commit()
                except Exception as e: st.error(f'Telegram failed: {e}')
    conn.close()
st.divider()
st.subheader('Research history')
try:
    c=connect()
    hist=pd.read_sql_query('SELECT timestamp, match_name, market, minute, probability, odds, edge, outcome FROM signals ORDER BY id DESC LIMIT 300',c)
    st.dataframe(hist,hide_index=True,use_container_width=True)
    c.close()
except sqlite3.Error as e: st.warning(str(e))
st.caption('Do not place real bets based on these unvalidated illustrative models. Next milestones: calibrated historical inputs, odds feed approval, background worker, robust deduplication and settlement.')
