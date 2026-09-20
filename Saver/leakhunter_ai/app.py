import os
import re
import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.enums import TA_LEFT
except Exception:
    colors = A4 = getSampleStyleSheet = ParagraphStyle = SimpleDocTemplate = Paragraph = Spacer = Table = TableStyle = PageBreak = TA_LEFT = None

import numpy as np
import pandas as pd
import streamlit as st

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz  # compatibility with older PyMuPDF releases
    except ImportError:
        fitz = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv('LEAKHUNTER_DATA_DIR', os.path.join(APP_DIR, 'data'))
DB_PATH = os.getenv('LEAKHUNTER_DB_PATH', os.path.join(DATA_DIR, 'leakhunter.db'))
DEMO_MODE = os.getenv('LEAKHUNTER_DEMO_MODE', '1').lower() in ('1', 'true', 'yes')

st.set_page_config(page_title='LeakHunter AI', page_icon='💧', layout='wide', initial_sidebar_state='expanded')

if not DEMO_MODE:
    st.session_state['demo_mode'] = False

st.markdown("""<style>
.block-container{padding-top:1.3rem;padding-bottom:2rem}
.lh-hero{padding:1.35rem 1.6rem;border-radius:20px;background:linear-gradient(135deg,#08111f 0%,#132640 58%,#0e7490 100%);color:white;margin-bottom:1rem;box-shadow:0 10px 30px rgba(2,8,23,.18)}
.lh-hero h1{margin:0;font-size:2.3rem;letter-spacing:-.02em}
.lh-hero p{margin:.4rem 0 0;color:#dbeafe;font-size:1rem}
.lh-pill{display:inline-block;padding:.22rem .65rem;border-radius:999px;background:#163554;color:#dbeafe;font-size:.78rem;margin:.65rem .35rem 0 0}
.lh-card{padding:1rem;border:1px solid #dbeafe;border-radius:16px;background:#f8fafc}
.lh-dark{padding:1.1rem 1.2rem;border-radius:16px;background:#0f172a;color:#f8fafc}
.lh-small{font-size:.83rem;color:#64748b}
</style>""", unsafe_allow_html=True)

os.makedirs(DATA_DIR, exist_ok=True)

# =========================================================
# LeakHunter AI v1.0 — SaaS foundation / Value Recovery OS
# =========================================================

DEMO_WORKSPACES = {
    'Demo Manufacturing Co.': {'currency': 'EGP', 'sector': 'Manufacturing'},
    'Demo Retail Group': {'currency': 'EGP', 'sector': 'Retail'},
    'Demo Enterprise': {'currency': 'USD', 'sector': 'Enterprise'},
}


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def money(v, currency):
    try:
        return f'{float(v):,.0f} {currency}'
    except Exception:
        return f'0 {currency}'


# ---------------- SQLite persistence ----------------
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def init_db():
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS organizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        sector TEXT,
        currency TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'analyst',
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(org_id, email),
        FOREIGN KEY(org_id) REFERENCES organizations(id)
    );
    CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER NOT NULL,
        opportunity_id TEXT NOT NULL,
        type TEXT,
        supplier_id TEXT,
        sku TEXT,
        reference TEXT,
        amount REAL,
        confidence TEXT,
        root_cause TEXT,
        evidence TEXT,
        action TEXT,
        status TEXT NOT NULL DEFAULT 'Potential',
        validated_amount REAL DEFAULT 0,
        realized_amount REAL DEFAULT 0,
        notes TEXT DEFAULT '',
        last_updated_at TEXT NOT NULL,
        UNIQUE(org_id, opportunity_id),
        FOREIGN KEY(org_id) REFERENCES organizations(id)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER NOT NULL,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        metadata TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(org_id) REFERENCES organizations(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER NOT NULL,
        user_id INTEGER,
        scan_name TEXT,
        source_mode TEXT,
        spend REAL,
        potential_value REAL,
        high_confidence_value REAL,
        opportunity_count INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(org_id) REFERENCES organizations(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')
    # Seed synthetic workspaces only in demo mode. A pilot can bootstrap its first
    # administrator once through environment variables (never through source code).
    if DEMO_MODE:
        for name, meta in DEMO_WORKSPACES.items():
            conn.execute('INSERT OR IGNORE INTO organizations(name,sector,currency,created_at) VALUES(?,?,?,?)',
                         (name, meta['sector'], meta['currency'], utc_now()))
        rows = conn.execute('SELECT id,name FROM organizations').fetchall()
        for row in rows:
            demo_password = os.getenv('LEAKHUNTER_DEMO_PASSWORD', 'LeakHunterDemo!')
            pwd_hash, salt = hash_password(demo_password)
            conn.execute('''INSERT OR IGNORE INTO users(org_id,email,display_name,role,password_hash,salt,created_at)
                            VALUES(?,?,?,?,?,?,?)''',
                         (row['id'], 'admin@demo.local', 'Demo Admin', 'admin', pwd_hash, salt, utc_now()))
    else:
        bootstrap_password = os.getenv('LEAKHUNTER_BOOTSTRAP_PASSWORD', '')
        if bootstrap_password:
            org_name = os.getenv('LEAKHUNTER_ORG_NAME', 'My Organization').strip()
            email = os.getenv('LEAKHUNTER_ADMIN_EMAIL', 'admin@example.com').strip().lower()
            conn.execute('INSERT OR IGNORE INTO organizations(name,sector,currency,created_at) VALUES(?,?,?,?)',
                         (org_name, os.getenv('LEAKHUNTER_ORG_SECTOR', 'Unclassified'), os.getenv('LEAKHUNTER_ORG_CURRENCY', 'EGP'), utc_now()))
            org_id = conn.execute('SELECT id FROM organizations WHERE name=?', (org_name,)).fetchone()['id']
            if not conn.execute('SELECT 1 FROM users WHERE org_id=? AND email=?', (org_id, email)).fetchone():
                pwd_hash, salt = hash_password(bootstrap_password)
                conn.execute('''INSERT INTO users(org_id,email,display_name,role,password_hash,salt,created_at)
                                VALUES(?,?,?,?,?,?,?)''', (org_id, email, 'Workspace Admin', 'admin', pwd_hash, salt, utc_now()))
    conn.commit()
    conn.close()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 120_000).hex()
    return digest, salt


def verify_password(password, digest, salt):
    check, _ = hash_password(password, salt)
    return secrets.compare_digest(check, digest)


def get_org(name):
    conn = db()
    row = conn.execute('SELECT * FROM organizations WHERE name=?', (name,)).fetchone()
    conn.close()
    return row


def authenticate(email, password, org_name):
    org = get_org(org_name)
    if not org:
        return None
    conn = db()
    row = conn.execute('SELECT * FROM users WHERE org_id=? AND email=?', (org['id'], email.strip().lower())).fetchone()
    conn.close()
    if row and verify_password(password, row['password_hash'], row['salt']):
        return row
    return None


def audit(org_id, user_id, action, entity_type='', entity_id='', metadata=''):
    conn = db()
    conn.execute('''INSERT INTO audit_log(org_id,user_id,action,entity_type,entity_id,metadata,created_at)
                    VALUES(?,?,?,?,?,?,?)''', (org_id, user_id, action, entity_type, entity_id, metadata, utc_now()))
    conn.commit(); conn.close()


def persist_opportunities(org_id, user_id, opportunities):
    if opportunities.empty:
        return
    conn = db()
    for _, row in opportunities.iterrows():
        conn.execute('''INSERT INTO opportunities(
            org_id,opportunity_id,type,supplier_id,sku,reference,amount,confidence,root_cause,evidence,action,status,
            validated_amount,realized_amount,notes,last_updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(org_id,opportunity_id) DO UPDATE SET
              amount=excluded.amount, confidence=excluded.confidence, root_cause=excluded.root_cause,
              evidence=excluded.evidence, action=excluded.action, last_updated_at=excluded.last_updated_at''',
            (org_id, row['opportunity_id'], row['type'], row['supplier_id'], row['sku'], row['reference'], float(row['amount']),
             row['confidence'], row['root_cause'], row['evidence'], row['action'], row.get('status','Potential'), 0, 0, '', utc_now()))
    conn.commit(); conn.close()
    audit(org_id, user_id, 'scan_saved', 'opportunity', '', f'Persisted {len(opportunities)} opportunities')


def load_ledger(org_id):
    conn = db()
    rows = conn.execute('SELECT * FROM opportunities WHERE org_id=? ORDER BY amount DESC', (org_id,)).fetchall()
    conn.close()
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def update_ledger(org_id, user_id, editable):
    conn = db()
    for _, r in editable.iterrows():
        conn.execute('''UPDATE opportunities SET status=?, validated_amount=?, realized_amount=?, notes=?, last_updated_at=?
                        WHERE org_id=? AND opportunity_id=?''',
                     (str(r.get('status','Potential')), float(r.get('validated_amount',0) or 0), float(r.get('realized_amount',0) or 0), str(r.get('notes','')), utc_now(), org_id, r['opportunity_id']))
    conn.commit(); conn.close()
    audit(org_id, user_id, 'ledger_updated', 'savings_ledger', '', f'Updated {len(editable)} opportunity records')


def save_scan(org_id, user_id, name, source_mode, spend, potential, high_conf, count):
    conn = db()
    conn.execute('''INSERT INTO scans(org_id,user_id,scan_name,source_mode,spend,potential_value,high_confidence_value,opportunity_count,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''', (org_id,user_id,name,source_mode,float(spend),float(potential),float(high_conf),int(count),utc_now()))
    conn.commit(); conn.close()


def scans_df(org_id):
    conn = db(); rows = conn.execute('SELECT * FROM scans WHERE org_id=? ORDER BY created_at DESC LIMIT 30', (org_id,)).fetchall(); conn.close()
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def audit_df(org_id):
    conn = db(); rows = conn.execute('''SELECT a.created_at,a.action,a.entity_type,a.entity_id,u.email,u.display_name,a.metadata
                                         FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
                                         WHERE a.org_id=? ORDER BY a.id DESC LIMIT 100''', (org_id,)).fetchall(); conn.close()
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


init_db()

# ---------------- Demo data ----------------
@st.cache_data
def demo_data():
    invoices = pd.DataFrame([
        ['INV-1001','SUP-001','PO-1001','SKU-A','Office Supplies','EA',1000,10.0,'EGP','2026-09-01'],
        ['INV-1002','SUP-001','PO-1002','SKU-B','IT Peripherals','EA',500,22.0,'EGP','2026-09-02'],
        ['INV-1003','SUP-002','PO-1003','SKU-C','Facilities','EA',100,55.0,'EGP','2026-09-03'],
        ['INV-1004','SUP-003','PO-1004','SKU-D','Logistics','EA',250,40.0,'EGP','2026-09-03'],
        ['INV-1004-DUP','SUP-003','PO-1004','SKU-D','Logistics','EA',250,40.0,'EGP','2026-09-04'],
        ['INV-1005','SUP-002','PO-1005','SKU-E','IT Peripherals','EA',1000,15.0,'EGP','2026-09-05'],
        ['INV-1006','SUP-004','PO-1006','SKU-F','Packaging','BOX',1000,12.0,'EGP','2026-09-06'],
        ['INV-1007','SUP-004','PO-1007','SKU-G','Packaging','BOX',1000,12.0,'EGP','2026-09-07'],
    ], columns=['invoice_id','supplier_id','po_id','sku','category','uom','quantity','unit_price','currency','invoice_date'])
    pos = pd.DataFrame([
        ['PO-1001','SUP-001','SKU-A','Office Supplies','EA',1000,10.0,'EGP','Approved'],
        ['PO-1002','SUP-001','SKU-B','IT Peripherals','EA',500,20.0,'EGP','Approved'],
        ['PO-1003','SUP-002','SKU-C','Facilities','EA',100,50.0,'EGP','Approved'],
        ['PO-1004','SUP-003','SKU-D','Logistics','EA',250,40.0,'EGP','Approved'],
        ['PO-1005','SUP-002','SKU-E','IT Peripherals','EA',900,15.0,'EGP','Approved'],
        ['PO-1006','SUP-004','SKU-F','Packaging','BOX',1000,10.0,'EGP','Approved'],
        ['PO-1007','SUP-004','SKU-G','Packaging','BOX',1000,10.0,'EGP','Approved'],
    ], columns=['po_id','supplier_id','sku','category','uom','ordered_qty','agreed_unit_price','currency','supplier_status'])
    receipts = pd.DataFrame([
        ['GR-1001','PO-1001','SUP-001','SKU-A',1000,'2026-09-02'],
        ['GR-1002','PO-1002','SUP-001','SKU-B',500,'2026-09-03'],
        ['GR-1003','PO-1003','SUP-002','SKU-C',100,'2026-09-04'],
        ['GR-1004','PO-1004','SUP-003','SKU-D',240,'2026-09-04'],
        ['GR-1005','PO-1005','SUP-002','SKU-E',900,'2026-09-06'],
        ['GR-1006','PO-1006','SUP-004','SKU-F',1000,'2026-09-07'],
        ['GR-1007','PO-1007','SUP-004','SKU-G',1000,'2026-09-08'],
    ], columns=['receipt_id','po_id','supplier_id','sku','received_qty','receipt_date'])
    contracts = pd.DataFrame([
        ['CON-001','SUP-001','SKU-A',10.0,3.0,0.0,60,'2026-01-01','2026-12-31','No',1000],
        ['CON-002','SUP-001','SKU-B',20.0,2.0,0.0,60,'2026-01-01','2026-12-31','No',500],
        ['CON-003','SUP-002','SKU-C',50.0,5.0,0.0,30,'2026-01-01','2026-12-31','No',100],
        ['CON-004','SUP-003','SKU-D',40.0,0.0,2.0,45,'2026-01-01','2026-12-31','No',250],
        ['CON-005','SUP-002','SKU-E',15.0,4.0,2.0,30,'2026-01-01','2026-12-31','No',900],
        ['CON-006','SUP-004','SKU-F',10.0,0.0,3.0,30,'2026-01-01','2026-12-31','No',1000],
        ['CON-007','SUP-004','SKU-G',10.0,0.0,3.0,30,'2026-01-01','2026-12-31','No',1000],
    ], columns=['contract_id','supplier_id','sku','contract_unit_price','rebate_pct','late_penalty_pct','payment_days','start_date','end_date','auto_renew','minimum_volume'])
    suppliers = pd.DataFrame([
        ['SUP-001','Alpha Office Ltd','EGY','Low','Yes',92,98,95,90],
        ['SUP-002','Bravo Facilities','EGY','Medium','Yes',84,87,72,88],
        ['SUP-003','Cargo Fast','EGY','High','No',76,93,58,62],
        ['SUP-004','Delta Packaging','EGY','Medium','Yes',81,79,82,71],
    ], columns=['supplier_id','supplier_name','country','declared_risk','cyber_reviewed','financial_score','quality_score','delivery_score','compliance_score'])
    return invoices, pos, receipts, contracts, suppliers


@st.cache_data(show_spinner=False)
def enterprise_demo_data():
    """Deterministic fictional enterprise dataset for customer demos.
    The values are synthetic and should never be presented as a real company dataset.
    """
    rng=np.random.default_rng(42)
    suppliers=[
        ('SUP-100','Cairo Industrial Supply','Egypt','Low',95,92,94,97),
        ('SUP-101','Nile Logistics Services','Egypt','Medium',86,89,68,91),
        ('SUP-102','Delta IT Distribution','Egypt','Medium',90,88,84,72),
        ('SUP-103','Alex Facilities Partners','Egypt','Low',88,94,79,89),
        ('SUP-104','Red Sea Packaging','Egypt','High',71,78,61,67),
        ('SUP-105','Prime MRO Solutions','Egypt','Medium',82,83,73,81),
        ('SUP-106','Global Office Products','UAE','Medium',84,91,88,86),
        ('SUP-107','Atlas Transport Group','Egypt','High',66,72,54,63),
        ('SUP-108','Vertex Security Systems','Egypt','Medium',92,86,81,90),
        ('SUP-109','Metro Catering & Services','Egypt','Low',94,90,93,95),
        ('SUP-110','Harbor Components','Egypt','Medium',79,81,76,74),
        ('SUP-111','Summit Cleaning Services','Egypt','Low',91,96,85,93),
    ]
    supplier_df=pd.DataFrame(suppliers,columns=['supplier_id','supplier_name','country','declared_risk','financial_score','quality_score','delivery_score','compliance_score'])
    supplier_df['cyber_reviewed']=supplier_df['declared_risk'].map({'Low':'Yes','Medium':'Yes','High':'No'})
    skus=[]
    cats=[('IT-ACCESS','IT Peripherals',120),('FAC-CLEAN','Facilities',65),('LOG-EXP','Logistics',210),('PKG-BOX','Packaging',18),('MRO-FLT','MRO',140),('OFF-SUP','Office Supplies',12),('SEC-ACC','Security',320),('CAT-SERV','Catering',90)]
    for i,(prefix,cat,base) in enumerate(cats,1):
        for j in range(1,6): skus.append((f'{prefix}-{j:02d}',cat,float(base*(0.85+rng.random()*0.3))))
    sku_df=pd.DataFrame(skus,columns=['sku','category','base_price'])
    inv_rows=[]; po_rows=[]; gr_rows=[]; contract_rows=[]
    invoice_idx=10000; po_idx=70000; gr_idx=50000; contract_idx=90000
    for n in range(320):
        sku,cat,base=skus[rng.integers(0,len(skus))]; supplier=supplier_df.iloc[rng.integers(0,len(supplier_df))]
        supplier_id=supplier.supplier_id
        po_id=f'PO-{po_idx}'; inv_id=f'INV-{invoice_idx}'; gr_id=f'GR-{gr_idx}'; contract_id=f'CON-{contract_idx}'
        ordered=int(rng.integers(20,700)); agreed=round(base*(0.92+rng.random()*0.16),2)
        # Inject business-like issues at meaningful rates.
        issue=rng.choice(['clean','price','qty','duplicate','contract','split'],p=[.68,.10,.07,.04,.08,.03])
        invoice_qty=ordered; unit_price=agreed
        if issue=='price': unit_price=round(agreed*(1.05+rng.random()*0.16),2)
        if issue=='qty': invoice_qty=ordered+int(rng.integers(5,max(6,ordered//8)))
        if issue=='contract': unit_price=round(agreed*(1.08+rng.random()*0.18),2)
        received=max(0,ordered-int(rng.integers(0,max(1,ordered//10))))
        if issue=='qty': received=ordered
        invoice_total=invoice_qty*unit_price
        currency='EGP'
        date=pd.Timestamp('2026-08-01')+pd.Timedelta(days=int(rng.integers(0,46)))
        inv_rows.append([inv_id,supplier_id,po_id,sku,cat,'EA',invoice_qty,unit_price,currency,date.strftime('%Y-%m-%d')])
        po_status='Approved'
        if issue=='split':
            ordered2=max(1,int(50000/agreed*0.96))
            ordered=min(ordered2, max(20,ordered))
        po_rows.append([po_id,supplier_id,sku,cat,'EA',ordered,agreed,currency,po_status])
        gr_rows.append([gr_id,po_id,supplier_id,sku,received,date.strftime('%Y-%m-%d')])
        contract_price=agreed if issue!='contract' else round(agreed*0.90,2)
        rebate=float(rng.choice([0,1.5,2.0,3.0],p=[.5,.2,.2,.1]))
        penalty=float(rng.choice([0,1,2],p=[.7,.2,.1]))
        contract_rows.append([contract_id,po_id,supplier_id,sku,contract_price,rebate,penalty,int(rng.choice([30,45,60])), '2026-01-01','2026-12-31','No',ordered])
        if issue=='duplicate': inv_rows.append([inv_id+'-DUP',supplier_id,po_id,sku,cat,'EA',invoice_qty,unit_price,currency,(date+pd.Timedelta(days=1)).strftime('%Y-%m-%d')])
        invoice_idx+=1; po_idx+=1; gr_idx+=1; contract_idx+=1
    invoices=pd.DataFrame(inv_rows,columns=['invoice_id','supplier_id','po_id','sku','category','uom','quantity','unit_price','currency','invoice_date'])
    pos=pd.DataFrame(po_rows,columns=['po_id','supplier_id','sku','category','uom','ordered_qty','agreed_unit_price','currency','supplier_status'])
    receipts=pd.DataFrame(gr_rows,columns=['receipt_id','po_id','supplier_id','sku','received_qty','receipt_date'])
    contracts=pd.DataFrame(contract_rows,columns=['contract_id','po_id','supplier_id','sku','contract_unit_price','rebate_pct','late_penalty_pct','payment_days','start_date','end_date','auto_renew','minimum_volume'])
    return invoices,pos,receipts,contracts,supplier_df


def build_executive_report(workspace, currency, invoice_spend, potential, high_conf, duplicate_value, rebate_opp, suppliers_count, opportunities, scores, annual_spend, target_leakage_pct, realization_pct, success_fee_pct, pilot_months):
    estimated=annual_spend*(target_leakage_pct/100)
    realized=estimated*(realization_pct/100)
    fee=realized*(success_fee_pct/100)
    net=realized-fee
    top=opportunities.head(8) if not opportunities.empty else pd.DataFrame()
    if SimpleDocTemplate is None:
        raise RuntimeError('reportlab is not installed.')
    buf=BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=28,leftMargin=28,topMargin=30,bottomMargin=30,title='LeakHunter AI Executive Savings Report')
    styles=getSampleStyleSheet(); styles.add(ParagraphStyle(name='LHTitle',parent=styles['Title'],fontSize=21,textColor=colors.HexColor('#0b1220'),spaceAfter=8)); styles.add(ParagraphStyle(name='LHSub',parent=styles['Normal'],fontSize=10,textColor=colors.HexColor('#475569'),spaceAfter=12)); styles.add(ParagraphStyle(name='LHHead',parent=styles['Heading2'],fontSize=13,textColor=colors.HexColor('#0e7490'),spaceBefore=10,spaceAfter=6)); styles.add(ParagraphStyle(name='LHBody',parent=styles['BodyText'],fontSize=9,leading=12,textColor=colors.HexColor('#1e293b')))
    story=[Paragraph('LeakHunter AI — Executive Savings Report',styles['LHTitle']),Paragraph(f'{workspace} · Confidential demo report · Generated {datetime.now():%Y-%m-%d %H:%M}',styles['LHSub'])]
    kpis=[['Spend analyzed',money(invoice_spend,currency),'Potential value',money(potential,currency)],['High-confidence',money(high_conf,currency),'Duplicate exposure',money(duplicate_value,currency)],['Rebate opportunity',money(rebate_opp,currency),'Suppliers analyzed',str(suppliers_count)]]
    t=Table(kpis,colWidths=[90,130,100,130]); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f1f5f9')),('BOX',(0,0),(-1,-1),0.5,colors.HexColor('#cbd5e1')),('INNERGRID',(0,0),(-1,-1),0.3,colors.HexColor('#cbd5e1')),('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)])); story += [t,Spacer(1,8),Paragraph('Executive interpretation',styles['LHHead']),Paragraph(f'LeakHunter identified {money(potential,currency)} of current transaction-level opportunity, including {money(high_conf,currency)} high-confidence findings. The demo model estimates an annualized opportunity of {money(estimated,currency)} at a {target_leakage_pct:.1f}% leakage assumption, with expected realized savings of {money(realized,currency)}.',styles['LHBody']),Paragraph('Top recovery opportunities',styles['LHHead'])]
    data=[['Type','Supplier','Reference','Value','Confidence']]
    for _,r in top.iterrows(): data.append([str(r['type']),str(r['supplier_id']),str(r['reference']),money(r['amount'],currency),str(r['confidence'])])
    if len(data)==1: data.append(['No findings','','','', ''])
    tt=Table(data,colWidths=[115,70,75,85,55]); tt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#0f172a')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),7.5),('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#cbd5e1')),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8fafc')])]))
    story += [tt,Paragraph('Pilot business case',styles['LHHead']),Paragraph(f'Illustrative {pilot_months}-month pilot: annual spend {money(annual_spend,currency)}, expected realization {realization_pct:.0f}%, example success fee {success_fee_pct:.0f}%, expected customer net benefit {money(net,currency)} after the example fee model.',styles['LHBody']),Paragraph('Decision note',styles['LHHead']),Paragraph('Use this report as a demo / decision-support artifact. Financial teams should validate source documents, contract terms, tax treatment, approval policies and realized savings before booking any benefit.',styles['LHBody'])]
    doc.build(story)
    return buf.getvalue()


# ---------------- Helpers ----------------
def ensure_col(df, col, default=None):
    if col not in df.columns:
        df[col] = default
    return df


def clean_numeric(df, cols):
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')
    return out


def read_uploaded_csv(uploaded, label, max_bytes=25 * 1024 * 1024):
    """Read an uploaded CSV with actionable, user-safe validation errors."""
    if getattr(uploaded, 'size', 0) > max_bytes:
        raise ValueError(f'{label} exceeds the 25 MB upload limit.')
    try:
        frame = pd.read_csv(uploaded)
    except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f'{label} is not a valid UTF-8 CSV: {exc}') from exc
    if frame.empty:
        raise ValueError(f'{label} contains no data rows.')
    normalized = [str(c).strip() for c in frame.columns]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f'{label} contains duplicate column names.')
    frame.columns = normalized
    return frame


def extract_pdf_text(file_bytes):
    if fitz is None:
        raise RuntimeError('PyMuPDF is not installed. Run pip install -r requirements.txt.')
    parts = []
    with fitz.open(stream=file_bytes, filetype='pdf') as doc:
        for page_no, page in enumerate(doc, start=1):
            parts.append(f'\n--- PAGE {page_no} ---\n{page.get_text()}')
    return '\n'.join(parts).strip()


def first_number(patterns, text):
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.M)
        if m:
            try: return float(m.group(1).replace(',', ''))
            except Exception: pass
    return None


def parse_contract_rules(text, supplier_id='FROM_PDF'):
    normalized = re.sub(r'[ \t]+', ' ', text)
    price = first_number([
        r'(?:unit price|price per unit|agreed price|contract price)\s*[:=\-]?\s*(?:EGP|USD|EUR|GBP|\$|€|£)?\s*([0-9][0-9,]*(?:\.\d+)?)',
        r'(?:EGP|USD|EUR|GBP|\$|€|£)\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:per unit|/unit)',
    ], normalized)
    rebate = first_number([r'(?:rebate|discount)\s*(?:of|:|=)?\s*([0-9]+(?:\.\d+)?)\s*%', r'([0-9]+(?:\.\d+)?)\s*%\s*(?:rebate|discount)'], normalized)
    payment_days = first_number([r'(?:net|payment terms?|payment within)\s*(?:of\s*)?(\d{1,3})\s*days', r'within\s*(\d{1,3})\s*days'], normalized)
    penalty = first_number([r'(?:late delivery penalty|late penalty|delivery penalty|penalty)\s*(?:of|:|=)?\s*([0-9]+(?:\.\d+)?)\s*%'], normalized)
    volume = first_number([r'(?:minimum order quantity|minimum volume|minimum annual volume)\s*(?:of|:|=)?\s*([0-9][0-9,]*)'], normalized)
    sku_match = re.search(r'(?:SKU|item code|product code)\s*[:#]?\s*([A-Z0-9][A-Z0-9._\-/]+)', normalized, re.I)
    rules=[]
    for field,value,label in [('contract_unit_price',price,'Contract unit price'),('rebate_pct',rebate,'Rebate / discount'),('payment_days',payment_days,'Payment terms'),('late_penalty_pct',penalty,'Late-delivery penalty'),('minimum_volume',volume,'Minimum volume')]:
        if value is not None: rules.append({'field':field,'value':value,'label':label})
    return {'supplier_id':supplier_id,'sku':sku_match.group(1).upper() if sku_match else None,'rules':rules,'raw_text':text,'text_preview':normalized[:6000]}


def apply_pdf_contract(parsed, contracts):
    row={'contract_id':f"PDF-{datetime.now():%Y%m%d%H%M%S}",'supplier_id':parsed['supplier_id'],'sku':parsed['sku'] or 'PDF-GENERIC','contract_unit_price':0.0,'rebate_pct':0.0,'late_penalty_pct':0.0,'payment_days':0.0,'start_date':None,'end_date':None,'auto_renew':None,'minimum_volume':0.0}
    for r in parsed['rules']:
        if r['field'] in row: row[r['field']]=r['value']
    return pd.concat([contracts,pd.DataFrame([row])],ignore_index=True)


def analyze(invoices, pos, receipts, contracts, tolerance_pct=2.0, approval_threshold=50000):
    """Reconcile invoice, PO, receipt and contract lines without double-counting exposure."""
    inv = clean_numeric(invoices.copy(), ['quantity', 'unit_price'])
    po = clean_numeric(pos.copy(), ['ordered_qty', 'agreed_unit_price'])
    gr = clean_numeric(receipts.copy(), ['received_qty'])
    ct = clean_numeric(contracts.copy(), ['contract_unit_price', 'rebate_pct', 'late_penalty_pct', 'payment_days', 'minimum_volume'])
    ensure_col(inv, 'currency', 'EGP'); ensure_col(po, 'currency', 'EGP')
    ensure_col(inv, 'category', 'Unclassified'); ensure_col(po, 'category', 'Unclassified')

    # A source file may contain many receipt rows per PO line.
    gr_agg = gr.groupby(['po_id', 'supplier_id', 'sku'], dropna=False, as_index=False)['received_qty'].sum()
    gr_agg['has_receipt'] = True
    m = inv.merge(po, on=['po_id', 'supplier_id', 'sku'], how='left', suffixes=('', '_po'))
    m = m.merge(gr_agg, on=['po_id', 'supplier_id', 'sku'], how='left')
    m['has_receipt'] = m['has_receipt'].fillna(False).astype(bool)

    # Prefer a PO-specific contract when supplied. Otherwise choose one deterministic,
    # currently effective supplier/SKU row to avoid a many-to-many merge explosion.
    contract_keys = ['supplier_id', 'sku']
    if 'po_id' in ct.columns and ct['po_id'].notna().any():
        contract_keys = ['po_id', 'supplier_id', 'sku']
    sort_cols = [c for c in ['end_date', 'start_date', 'contract_id'] if c in ct.columns]
    if sort_cols:
        ct = ct.sort_values(sort_cols)
    ct = ct.drop_duplicates(contract_keys, keep='last')
    m = m.merge(ct, on=contract_keys, how='left', suffixes=('', '_contract'))

    m['invoice_total'] = m['quantity'] * m['unit_price']
    m['po_price_gap_pct'] = np.where(m['agreed_unit_price'] > 0, (m['unit_price'] - m['agreed_unit_price']) / m['agreed_unit_price'] * 100, np.nan)
    m['contract_price_gap_pct'] = np.where(m['contract_unit_price'] > 0, (m['unit_price'] - m['contract_unit_price']) / m['contract_unit_price'] * 100, np.nan)

    # Use the strongest available commercial baseline and split supported-price
    # exposure from unsupported-quantity exposure, so one unit is never counted twice.
    m['baseline_unit_price'] = m[['agreed_unit_price', 'contract_unit_price']].min(axis=1, skipna=True)
    m['expected_qty'] = m['ordered_qty']
    receipt_mask = m['has_receipt'] & m['received_qty'].notna()
    m.loc[receipt_mask, 'expected_qty'] = m.loc[receipt_mask, ['ordered_qty', 'received_qty']].min(axis=1, skipna=True)
    m['supported_qty'] = m[['quantity', 'expected_qty']].min(axis=1, skipna=True).clip(lower=0)
    m['unsupported_qty'] = (m['quantity'] - m['expected_qty']).clip(lower=0).fillna(0)
    price_gap = (m['unit_price'] - m['baseline_unit_price']).clip(lower=0).fillna(0)
    tolerance_mask = m['baseline_unit_price'].gt(0) & (price_gap / m['baseline_unit_price'] * 100).gt(tolerance_pct)
    m['price_variance_value'] = np.where(tolerance_mask, price_gap * m['supported_qty'].fillna(0), 0.0)
    m['quantity_variance_value'] = (m['unsupported_qty'] * m['baseline_unit_price'].fillna(m['unit_price'])).fillna(0)
    m['po_qty_overage'] = (m['quantity'] - m['ordered_qty']).clip(lower=0).fillna(0)
    m['receipt_qty_overage'] = np.where(receipt_mask, (m['quantity'] - m['received_qty']).clip(lower=0), 0.0)
    m['po_qty_overage_value'] = m['quantity_variance_value']
    m['receipt_qty_overage_value'] = np.where(receipt_mask, m['quantity_variance_value'], 0.0)
    m['contract_price_variance_value'] = np.where(m['contract_unit_price'].gt(0), ((m['unit_price'] - m['contract_unit_price']).clip(lower=0) * m['supported_qty']).fillna(0), 0.0)
    m['contract_rebate_value'] = np.where(m['rebate_pct'].notna() & m['rebate_pct'].gt(0), m['invoice_total'] * m['rebate_pct'] / 100, 0.0)
    m['currency_mismatch'] = m['currency'].fillna('') != m.get('currency_po', m['currency']).fillna(m['currency'].fillna(''))

    dup_keys = ['supplier_id', 'po_id', 'sku', 'quantity', 'unit_price', 'currency']
    order_cols = [c for c in ['invoice_date', 'invoice_id'] if c in m.columns]
    ordered_index = m.sort_values(order_cols or ['invoice_id']).index
    duplicate_rank = m.loc[ordered_index].groupby(dup_keys, dropna=False).cumcount()
    m['possible_duplicate'] = False
    m.loc[ordered_index, 'possible_duplicate'] = duplicate_rank.gt(0).to_numpy()
    m['duplicate_exposure'] = np.where(m['possible_duplicate'], m['invoice_total'], 0.0)

    po2 = po.copy(); po2['po_value'] = po2['ordered_qty'] * po2['agreed_unit_price']
    po2['near_threshold'] = po2['po_value'].between(approval_threshold * .85, approval_threshold, inclusive='left')
    split_group = ['supplier_id', 'sku']
    if 'po_date' in po2.columns:
        po2['_period'] = pd.to_datetime(po2['po_date'], errors='coerce').dt.to_period('30D').astype(str)
        split_group.append('_period')
    near = po2.groupby(split_group, dropna=False)['near_threshold'].transform('sum')
    po2['possible_split_po'] = po2['near_threshold'] & near.ge(2)
    split_map = po2.set_index('po_id')['possible_split_po'].to_dict()
    m['possible_split_po'] = m['po_id'].map(split_map).fillna(False)
    approved = po.set_index('po_id').get('supplier_status', pd.Series(dtype='object')).to_dict()
    m['off_contract'] = m['po_id'].map(lambda x: str(approved.get(x, 'Approved')).strip().lower() != 'approved')

    flags=[]; evidence=[]; confidence=[]; root_causes=[]; actions=[]
    for _, r in m.iterrows():
        f=[]; e=[]; c='Low'; root='No material exception'; action='Review source documents.'
        if r['price_variance_value'] > 0: f.append('Invoice > agreed price'); e.append(f"Invoice {r['unit_price']:.2f} vs baseline {r['baseline_unit_price']:.2f}"); c='High'; root='Pricing mismatch'; action='Validate the applicable rate and recover the difference.'
        if not r['has_receipt']: f.append('Missing receipt record'); e.append('No matching goods receipt was supplied'); c='Medium' if c == 'Low' else c
        elif r['receipt_qty_overage'] > 0: f.append('Invoice > received qty'); e.append(f"Invoice {r['quantity']:.0f} vs received {r['received_qty']:.0f}"); c='High'; root='Unreceived quantity'; action='Hold unsupported quantity and request evidence or credit.'
        if r['po_qty_overage'] > 0: f.append('Invoice > PO qty'); e.append(f"Invoice {r['quantity']:.0f} vs PO {r['ordered_qty']:.0f}"); c='High'; root='Quantity overage'; action='Validate approval for excess quantity.'
        if pd.notna(r.get('contract_unit_price')) and r['contract_price_variance_value'] > 0: f.append('Invoice > contract price')
        if r['possible_duplicate']: f.append('Possible duplicate'); e.append('A prior invoice has the same supplier, PO, SKU, quantity, unit price and currency'); c='High'; root='Duplicate payment risk'; action='Stop/verify payment and compare source documents.'
        if r['possible_split_po']: f.append('Possible split PO'); e.append('Multiple related POs are just below the approval threshold'); c='Medium' if c=='Low' else c
        if r['off_contract']: f.append('Maverick spend'); e.append('PO supplier status is not approved'); c='Medium' if c=='Low' else c
        flags.append(', '.join(f)); evidence.append(' | '.join(e)); confidence.append(c); root_causes.append(root); actions.append(action)
    m['flags']=flags; m['evidence']=evidence; m['confidence']=confidence; m['root_cause']=root_causes; m['recommended_action']=actions
    m['potential_leakage'] = m['price_variance_value'] + m['quantity_variance_value'] + m['duplicate_exposure']
    return m, po2


def supplier_benchmark(pos):
    po=clean_numeric(pos.copy(),['ordered_qty','agreed_unit_price']).dropna(subset=['sku','supplier_id','agreed_unit_price']); stats=po.groupby('sku')['agreed_unit_price'].agg(['median','mean','min','max']).reset_index(); out=po.merge(stats,on='sku',how='left'); out['price_vs_peer_pct']=np.where(out['median']>0,(out['agreed_unit_price']-out['median'])/out['median']*100,0); out['benchmark_opportunity']=np.where(out['price_vs_peer_pct']>0,(out['agreed_unit_price']-out['median']).clip(lower=0)*out['ordered_qty'].fillna(0),0); return out,stats


def supplier_score(results,suppliers):
    sp=suppliers.copy();
    for c in ['financial_score','quality_score','delivery_score','compliance_score']: ensure_col(sp,c,70); sp[c]=pd.to_numeric(sp[c],errors='coerce').fillna(70)
    metrics=results.groupby('supplier_id').agg(spend=('invoice_total','sum'),leakage=('potential_leakage','sum'),duplicates=('possible_duplicate','sum'),exceptions=('flags',lambda s:int((s!='').sum()))).reset_index(); sp=sp.merge(metrics,on='supplier_id',how='left').fillna({'spend':0,'leakage':0,'duplicates':0,'exceptions':0}); sp['leakage_rate_pct']=np.where(sp['spend']>0,sp['leakage']/sp['spend']*100,0); sp['operational_score']=(sp['quality_score']*.35+sp['delivery_score']*.35+sp['compliance_score']*.30)-sp['leakage_rate_pct']*2; sp['trust_score']=(sp['financial_score']*.25+sp['operational_score']*.45+sp['compliance_score']*.30).clip(0,100); sp['risk_band']=pd.cut(sp['trust_score'],bins=[-1,50,70,85,101],labels=['Critical','High','Medium','Low']); return sp


def build_opportunities(results,benchmark):
    rows=[]
    for _,r in results.iterrows():
        amount=float(r.get('potential_leakage',0) or 0)
        if amount>0: rows.append({'opportunity_id':f"LEAK-{r['invoice_id']}",'type':'Transaction leakage','supplier_id':r['supplier_id'],'sku':r['sku'],'reference':r['invoice_id'],'amount':amount,'confidence':r['confidence'],'root_cause':r['root_cause'],'evidence':r['evidence'],'action':r['recommended_action'],'status':'Potential'})
        if r.get('contract_rebate_value',0)>0: rows.append({'opportunity_id':f"REBATE-{r['invoice_id']}",'type':'Unclaimed rebate','supplier_id':r['supplier_id'],'sku':r['sku'],'reference':r['invoice_id'],'amount':float(r['contract_rebate_value']),'confidence':'Medium','root_cause':'Contract entitlement','evidence':f"Rebate clause {r['rebate_pct']:.1f}% on invoice total {r['invoice_total']:.2f}",'action':'Validate earned rebate and raise supplier claim.','status':'Potential'})
    for _,r in benchmark.iterrows():
        amt=float(r.get('benchmark_opportunity',0) or 0)
        if amt>0: rows.append({'opportunity_id':f"BENCH-{r['po_id']}",'type':'Benchmark / negotiation','supplier_id':r['supplier_id'],'sku':r['sku'],'reference':r['po_id'],'amount':amt,'confidence':'Medium' if r['price_vs_peer_pct']<10 else 'High','root_cause':'Above-peer price','evidence':f"PO price {r['agreed_unit_price']:.2f}; peer median {r['median']:.2f}; gap {r['price_vs_peer_pct']:.1f}%",'action':f"Negotiate toward {r['median']:.2f} or document premium.",'status':'Potential'})
    out=pd.DataFrame(rows)
    return out.sort_values('amount',ascending=False) if not out.empty else pd.DataFrame(columns=['opportunity_id','type','supplier_id','sku','reference','amount','confidence','root_cause','evidence','action','status'])


def negotiation_script(row,currency):
    return f"""Subject: Commercial review — {row.get('supplier_id','Supplier')} / {row.get('sku','SKU')}\n\nWe identified a commercial variance requiring review. Current opportunity value is approximately {float(row.get('amount',0)):,.0f} {currency}.\n\nEvidence: {row.get('evidence','')}\n\nPlease confirm the applicable contractual basis and agree the corrective action (credit note, price correction, rebate settlement, or revised pricing).\n\nRegards,\nProcurement Team"""


def claim_pack(row,workspace):
    return f"""LEAKHUNTER AI — RECOVERY CLAIM PACK\nWorkspace: {workspace}\nGenerated: {datetime.now():%Y-%m-%d %H:%M}\n\nOpportunity ID: {row['opportunity_id']}\nType: {row['type']}\nSupplier: {row['supplier_id']}\nSKU: {row['sku']}\nReference: {row['reference']}\nAmount: {row['amount']:,.2f}\nConfidence: {row['confidence']}\nRoot cause: {row['root_cause']}\n\nEVIDENCE\n{row['evidence']}\n\nRECOMMENDED ACTION\n{row['action']}\n\nSTATUS\n{row['status']}\n\nAnalytical decision-support only. Validate against source records before financial action.\n"""



def landing_page():
    st.markdown("""<div class='lh-hero'><h1>LeakHunter AI</h1><p><b>From spend data to recovered cash.</b><br>Evidence-backed procurement value recovery for CFOs, procurement teams and finance leaders.</p><span class='lh-pill'>Find leakage</span><span class='lh-pill'>Prove evidence</span><span class='lh-pill'>Recover value</span><span class='lh-pill'>Track realized savings</span></div>""", unsafe_allow_html=True)
    a,b,c=st.columns(3)
    a.markdown("**💰 Find money**\n\nDetect pricing variance, duplicates, unreceived quantities, contract leakage, maverick spend and supplier benchmark gaps.")
    b.markdown("**🔎 Prove it**\n\nTrace every finding back to Invoice → PO → Receipt → Contract evidence.")
    c.markdown("**📒 Recover it**\n\nCreate negotiation packs and track Potential → Validated → Realized savings.")
    st.markdown('## See the product in action')
    left,right=st.columns([1,1])
    with left:
        if DEMO_MODE and st.button('🚀 Launch Customer Demo', width='stretch', type='primary'):
            demo_org = get_org('Demo Enterprise')
            st.session_state.demo_mode=True; st.session_state.authenticated=True; st.session_state.user={'id':0,'org_id':demo_org['id'],'display_name':'Customer Demo','role':'demo','email':'demo@leakhunter.local'}; st.session_state.org_name='Demo Enterprise'; audit(demo_org['id'],None,'customer_demo_started','session','', 'Customer demo mode'); st.rerun()
        elif not DEMO_MODE:
            st.info('Customer demo mode is disabled for this deployment.')
    with right:
        if st.button('🔐 Sign in to Workspace', width='stretch'):
            st.session_state.view='login'; st.rerun()
    st.markdown('### Why teams buy it')
    st.info('LeakHunter is positioned around measurable value recovery rather than “AI for AI’s sake”: every opportunity carries an amount, evidence, confidence, recommended action and savings status.')
    st.markdown('### Demo journey')
    st.code('Spend data → Detect leakage → Evidence chain → Negotiation pack → Realized savings ledger',language='text')


def login_screen():
    st.markdown("""<div class='lh-hero'><h1>Workspace sign in</h1><p>Demo credentials are for local evaluation only.</p></div>""", unsafe_allow_html=True)
    conn = db(); org_names = [r['name'] for r in conn.execute('SELECT name FROM organizations ORDER BY name').fetchall()]; conn.close()
    if not org_names:
        st.error('No workspace is configured. Set the pilot bootstrap environment variables and restart the service.')
        return
    with st.form('login_form'):
        org = st.selectbox('Organization', org_names)
        email = st.text_input('Email', 'admin@demo.local' if DEMO_MODE else '')
        password = st.text_input('Password', type='password')
        submitted = st.form_submit_button('Sign in', width='stretch')
    if submitted:
        user = authenticate(email,password,org)
        if user:
            st.session_state.authenticated=True; st.session_state.user=dict(user); st.session_state.org_name=org; st.session_state.demo_mode=False
            audit(user['org_id'],user['id'],'login','session','',f'Login from local prototype'); st.rerun()
        st.error('Invalid demo credentials.')
    if DEMO_MODE:
        st.info('Demo email: admin@demo.local. Use the password configured in LEAKHUNTER_DEMO_PASSWORD.')
    if st.button('← Back to landing page'):
        st.session_state.view='landing'; st.rerun()


if not st.session_state.get('authenticated'):
    if st.session_state.get('view','landing')=='login': login_screen()
    else: landing_page()
    st.stop()

user=st.session_state['user']; workspace=st.session_state['org_name']; org=get_org(workspace)
if org is None:
    st.error('The selected workspace no longer exists. Please contact an administrator.')
    st.session_state.clear(); st.stop()
company_meta={'currency': org['currency'] or 'EGP', 'sector': org['sector'] or 'Unclassified'}
db_user_id=user.get('id') if user.get('id', 0) > 0 else None
# ---------------- Sidebar ----------------
st.sidebar.title('💧 LeakHunter AI')
st.sidebar.caption('AI Value Recovery OS · Commercial MVP v1.0')
st.sidebar.write(f"**{user['display_name']}** · {user['role']}")
st.sidebar.write(f"**{workspace}**")
if st.sidebar.button('Sign out / Exit Demo'):
    if user.get('id',0): audit(org['id'],user['id'],'logout','session','', 'User signed out')
    st.session_state.clear(); st.session_state.view='landing'; st.rerun()
if st.session_state.get('demo_mode'):
    st.sidebar.warning('Customer Demo Mode — synthetic data only. Uploads and production credentials are disabled.')

input_mode='Demo data' if st.session_state.get('demo_mode') else st.sidebar.radio('Data mode',['Demo data','Upload CSVs'])
if input_mode=='Demo data': invoices,pos,receipts,contracts,suppliers = enterprise_demo_data() if st.session_state.get('demo_mode') else demo_data()
else:
    inv_file=st.sidebar.file_uploader('Invoices CSV',type=['csv']); po_file=st.sidebar.file_uploader('Purchase Orders CSV',type=['csv']); receipt_file=st.sidebar.file_uploader('Goods Receipts CSV',type=['csv']); contract_file=st.sidebar.file_uploader('Contracts CSV',type=['csv']); supplier_file=st.sidebar.file_uploader('Suppliers CSV (optional)',type=['csv'])
    if not all([inv_file,po_file,receipt_file,contract_file]): st.info('Upload Invoices, POs, Goods Receipts and Contracts to start.'); st.stop()
    try:
        invoices=read_uploaded_csv(inv_file, 'Invoices'); pos=read_uploaded_csv(po_file, 'Purchase Orders')
        receipts=read_uploaded_csv(receipt_file, 'Goods Receipts'); contracts=read_uploaded_csv(contract_file, 'Contracts')
        suppliers=read_uploaded_csv(supplier_file, 'Suppliers') if supplier_file else pd.DataFrame(columns=['supplier_id','supplier_name'])
    except ValueError as exc:
        st.error(str(exc)); st.stop()

st.sidebar.subheader('Controls')
currency=st.sidebar.text_input('Display currency',company_meta['currency']); tolerance=st.sidebar.slider('Variance tolerance %',0.0,10.0,2.0,0.5); approval_threshold=st.sidebar.number_input('Approval threshold',min_value=1.0,value=50000.0,step=5000.0)

st.sidebar.subheader('Contract AI')
pdf_files=None if st.session_state.get('demo_mode') else st.sidebar.file_uploader('Optional contract PDFs',type=['pdf'],accept_multiple_files=True); pdf_supplier=st.sidebar.text_input('PDF supplier ID','SUP-PDF',disabled=st.session_state.get('demo_mode')); parsed_pdfs=[]
if pdf_files:
    for pf in pdf_files:
        try:
            parsed=parse_contract_rules(extract_pdf_text(pf.getvalue()),supplier_id=pdf_supplier); parsed_pdfs.append((pf.name,parsed)); contracts=apply_pdf_contract(parsed,contracts)
        except Exception as exc: st.sidebar.error(f'{pf.name}: {exc}')

# ---------------- Schema ----------------
for df in [invoices,pos,receipts,contracts]: df.columns=[str(c).strip() for c in df.columns]
required={'Invoices':{'invoice_id','supplier_id','po_id','sku','quantity','unit_price'},'Purchase Orders':{'po_id','supplier_id','sku','ordered_qty','agreed_unit_price'},'Goods Receipts':{'receipt_id','po_id','supplier_id','sku','received_qty'},'Contracts':{'contract_id','supplier_id','sku','contract_unit_price','rebate_pct','late_penalty_pct','payment_days'}}
for label,df in [('Invoices',invoices),('Purchase Orders',pos),('Goods Receipts',receipts),('Contracts',contracts)]:
    missing=required[label]-set(df.columns)
    if missing: st.error(f'{label} missing columns: {sorted(missing)}'); st.stop()
    key_cols = [c for c in required[label] if c.endswith('_id') or c in ('sku',)]
    if key_cols and df[key_cols].isna().any().any(): st.error(f'{label} contains blank required identifiers.'); st.stop()

numeric_rules=[('Invoices',invoices,['quantity','unit_price']),('Purchase Orders',pos,['ordered_qty','agreed_unit_price']),('Goods Receipts',receipts,['received_qty']),('Contracts',contracts,['contract_unit_price','rebate_pct','late_penalty_pct','payment_days'])]
for label, frame, columns in numeric_rules:
    for column in columns:
        values=pd.to_numeric(frame[column],errors='coerce')
        if values.isna().any(): st.error(f'{label}.{column} contains blank or non-numeric values.'); st.stop()
        if values.lt(0).any(): st.error(f'{label}.{column} cannot contain negative values.'); st.stop()

results,po_enriched=analyze(invoices,pos,receipts,contracts,tolerance_pct=tolerance,approval_threshold=approval_threshold)
benchmark,benchmark_stats=supplier_benchmark(pos); scores=supplier_score(results,suppliers if not suppliers.empty else pd.DataFrame(columns=['supplier_id','supplier_name'])); opportunities=build_opportunities(results,benchmark)

# Persist latest scan + opportunities when requested
invoice_spend=float(results['invoice_total'].sum()); potential=float(opportunities['amount'].sum()) if not opportunities.empty else 0.0; high_conf=float(opportunities.loc[opportunities['confidence'].eq('High'),'amount'].sum()) if not opportunities.empty else 0.0; duplicate_value=float(results.loc[results['possible_duplicate'],'invoice_total'].sum()); rebate_opp=float(results['contract_rebate_value'].sum())

if st.sidebar.button('💾 Save scan to workspace'):
    persist_opportunities(org['id'],db_user_id,opportunities)
    save_scan(org['id'],db_user_id,f'{input_mode} scan {datetime.now():%Y-%m-%d %H:%M}',input_mode,invoice_spend,potential,high_conf,len(opportunities))
    st.sidebar.success('Scan and opportunities saved.')

st.markdown("""<div class='lh-hero'><h1>💧 LeakHunter AI</h1><p>From spend data to recovered cash.</p><span class='lh-pill'>Find the money</span><span class='lh-pill'>Prove the evidence</span><span class='lh-pill'>Recover the value</span><span class='lh-pill'>Track realized savings</span></div>""", unsafe_allow_html=True)
c1,c2,c3,c4,c5,c6=st.columns(6); c1.metric('Spend analyzed',money(invoice_spend,currency)); c2.metric('Potential value',money(potential,currency)); c3.metric('High-confidence',money(high_conf,currency)); c4.metric('Duplicate value',money(duplicate_value,currency)); c5.metric('Rebate opportunity',money(rebate_opp,currency)); c6.metric('Suppliers',f'{len(scores):,}')

# ---------------- Tabs ----------------
tabs=st.tabs(['🏢 CFO Home','💰 Find Savings','🧾 Investigations','🤝 Supplier 360','📄 Contract AI','🛡️ Controls','💬 Negotiate & Recover','📊 Spend Intelligence','📒 Savings Ledger','💼 ROI & Pilot','📑 Executive Report','🧑‍💼 Admin & Audit'])

with tabs[0]:
    st.markdown(f'### Executive Command Center · {workspace}')
    if opportunities.empty: st.success('No savings opportunities found.')
    else:
        top3=opportunities.head(5).copy(); a,b=st.columns([1.2,1])
        with a:
            st.markdown('#### Top opportunities'); st.dataframe(top3[['opportunity_id','type','supplier_id','sku','amount','confidence','status']],width='stretch',hide_index=True)
        with b:
            st.markdown('#### Value by type'); st.bar_chart(opportunities.groupby('type')['amount'].sum().sort_values(ascending=False))
        st.markdown('#### Recommended executive actions'); actions=[{'Priority':'P1' if r['confidence']=='High' else 'P2','Action':r['action'],'Value':r['amount'],'Evidence':r['evidence']} for _,r in top3.iterrows()]; st.dataframe(pd.DataFrame(actions),width='stretch',hide_index=True)
    scans=scans_df(org['id'])
    if not scans.empty:
        st.markdown('#### Saved scan history'); st.dataframe(scans,width='stretch',hide_index=True)

with tabs[1]:
    st.markdown('### Find Savings')
    if opportunities.empty: st.success('No opportunities found.')
    else:
        st.dataframe(opportunities[['opportunity_id','type','supplier_id','sku','reference','amount','confidence','root_cause','status']],width='stretch',hide_index=True)
        st.download_button('Download opportunity register',opportunities.to_csv(index=False).encode(),file_name='leakhunter_opportunities.csv',mime='text/csv')
        st.markdown('#### Value by opportunity type'); st.bar_chart(opportunities.groupby('type')['amount'].sum().sort_values(ascending=False))

with tabs[2]:
    st.markdown('### Investigation Queue'); finding_types=['Invoice > agreed price','Missing receipt record','Invoice > received qty','Invoice > PO qty','Invoice > contract price','Possible duplicate','Possible split PO','Maverick spend']; f=st.multiselect('Finding type',finding_types,default=finding_types[:5]); mask=results['flags'].apply(lambda x:any(k in x for k in f)) if f else pd.Series(True,index=results.index); cols=['invoice_id','supplier_id','po_id','sku','category','quantity','ordered_qty','received_qty','unit_price','agreed_unit_price','contract_unit_price','invoice_total','potential_leakage','confidence','root_cause','flags','evidence','recommended_action']; st.dataframe(results.loc[mask,cols],width='stretch',hide_index=True)
    st.caption('Every finding is a lead. Recovery decisions require document-level validation.')

with tabs[3]:
    st.markdown('### Supplier 360')
    if scores.empty: st.info('Upload a Suppliers CSV to activate Supplier 360.')
    else:
        name_map=scores.set_index('supplier_id')['supplier_name'].to_dict() if 'supplier_name' in scores.columns else {}; pick=st.selectbox('Supplier',scores['supplier_id'].tolist(),format_func=lambda x:f"{x} — {name_map.get(x,'')}"); s=scores[scores['supplier_id']==pick].iloc[0]; m1,m2,m3,m4=st.columns(4); m1.metric('Trust score',f"{s['trust_score']:.0f}/100"); m2.metric('Spend',money(s['spend'],currency)); m3.metric('Leakage',money(s['leakage'],currency)); m4.metric('Risk band',str(s['risk_band'])); supplier_opps=opportunities[opportunities['supplier_id']==pick] if not opportunities.empty else pd.DataFrame(); left,right=st.columns(2)
        with left: st.markdown('#### Scorecard'); st.dataframe(pd.DataFrame([{'Metric':'Financial','Score':s['financial_score']},{'Metric':'Quality','Score':s['quality_score']},{'Metric':'Delivery','Score':s['delivery_score']},{'Metric':'Compliance','Score':s['compliance_score']}]),width='stretch',hide_index=True)
        with right: st.markdown('#### Value at stake'); st.write(f"Leakage rate: **{s['leakage_rate_pct']:.2f}%**"); st.write(f"Open opportunities: **{len(supplier_opps)}**"); st.write(f"Cyber review: **{s.get('cyber_reviewed','Unknown')}**"); st.write(f"Declared risk: **{s.get('declared_risk','Unknown')}**")
        if not supplier_opps.empty: st.dataframe(supplier_opps[['opportunity_id','type','sku','reference','amount','confidence','status']],width='stretch',hide_index=True)

with tabs[4]:
    st.markdown('### Contract Intelligence')
    for name,parsed in parsed_pdfs:
        st.write(f"**{name}** · Supplier: {parsed['supplier_id']} · SKU: {parsed['sku'] or 'Generic'}");
        if parsed['rules']: st.dataframe(pd.DataFrame(parsed['rules']),width='stretch',hide_index=True)
        with st.expander('Evidence preview'): st.text(parsed['text_preview'])
    st.dataframe(contracts,width='stretch',hide_index=True)

with tabs[5]:
    st.markdown('### Controls, Fraud & Leakage Signals')
    split_count=int(results['possible_split_po'].sum()); maverick_count=int(results['off_contract'].sum()); fx_count=int(results['currency_mismatch'].sum())
    controls=[{'control':'Duplicate invoice value','value':duplicate_value,'severity':'High' if duplicate_value>0 else 'Low','action':'Hold / verify duplicate candidates'},{'control':'Possible split-PO lines','value':split_count,'severity':'Medium' if split_count else 'Low','action':'Review approval-threshold patterns'},{'control':'Maverick spend lines','value':maverick_count,'severity':'Medium' if maverick_count else 'Low','action':'Review approved supplier policy'},{'control':'Non-base currency lines','value':fx_count,'severity':'Review' if fx_count else 'Low','action':'Normalize FX before benchmarking'}]; st.dataframe(pd.DataFrame(controls),width='stretch',hide_index=True)

with tabs[6]:
    st.markdown('### Negotiate & Recover')
    if opportunities.empty: st.info('No recovery candidates.')
    else:
        idx=st.selectbox('Choose opportunity',opportunities.index.tolist(),format_func=lambda x:f"{opportunities.loc[x,'opportunity_id']} · {opportunities.loc[x,'type']} · {money(opportunities.loc[x,'amount'],currency)}"); row=opportunities.loc[idx]; st.write(f"**{row['type']}** · **{row['supplier_id']}** · {row['sku']} · {money(row['amount'],currency)} · {row['confidence']} confidence"); left,right=st.columns(2)
        with left: st.markdown('#### Evidence'); st.info(row['evidence'] or 'No evidence text captured.'); st.markdown('#### Recommended action'); st.write(row['action'])
        with right:
            script=negotiation_script(row,currency); st.text_area('Negotiation Copilot draft',script,height=220); st.download_button('Download negotiation draft',script.encode(),file_name=f"negotiation_{row['opportunity_id']}.txt",mime='text/plain')
        pack=claim_pack(row,workspace); st.download_button('Download Recovery Claim Pack',pack.encode(),file_name=f"claim_pack_{row['opportunity_id']}.txt",mime='text/plain')
        st.success('Every opportunity can become a negotiation case with evidence, amount and recommended action.')

with tabs[7]:
    st.markdown('### Spend Intelligence'); spend_by_supplier=results.groupby('supplier_id',dropna=False).agg(invoice_spend=('invoice_total','sum'),invoices=('invoice_id','count'),leakage=('potential_leakage','sum')).reset_index().sort_values('invoice_spend',ascending=False); spend_by_supplier['leakage_rate_pct']=np.where(spend_by_supplier['invoice_spend']>0,spend_by_supplier['leakage']/spend_by_supplier['invoice_spend']*100,0); st.dataframe(spend_by_supplier,width='stretch',hide_index=True); root=results[results['potential_leakage']>0].groupby('root_cause')['potential_leakage'].sum().sort_values(ascending=False); st.bar_chart(root) if not root.empty else None; st.markdown('#### Peer-price benchmark'); st.dataframe(benchmark[['supplier_id','po_id','sku','agreed_unit_price','median','min','max','price_vs_peer_pct','benchmark_opportunity']].sort_values('benchmark_opportunity',ascending=False),width='stretch',hide_index=True)

with tabs[8]:
    st.markdown('### Savings Ledger · persistent')
    saved=load_ledger(org['id'])
    if saved.empty:
        if not opportunities.empty: persist_opportunities(org['id'],db_user_id,opportunities); saved=load_ledger(org['id'])
    if not saved.empty:
        editable=st.data_editor(saved[['opportunity_id','type','supplier_id','reference','amount','confidence','status','validated_amount','realized_amount','notes']],width='stretch',hide_index=True,num_rows='fixed',column_config={'status':st.column_config.SelectboxColumn(options=['Potential','Validated','Realized','Rejected']),'validated_amount':st.column_config.NumberColumn(min_value=0,format='%.2f'),'realized_amount':st.column_config.NumberColumn(min_value=0,format='%.2f'),'notes':st.column_config.TextColumn()})
        if st.button('💾 Commit ledger changes'):
            update_ledger(org['id'],db_user_id,editable); st.success('Ledger changes saved to SQLite.'); st.rerun()
        a,b,c,d=st.columns(4); a.metric('Potential',money(editable['amount'].sum(),currency)); b.metric('Validated',money(editable['validated_amount'].sum(),currency)); c.metric('Realized',money(editable['realized_amount'].sum(),currency)); d.metric('Rejected',money(editable.loc[editable['status'].eq('Rejected'),'amount'].sum(),currency)); st.download_button('Download savings ledger',editable.to_csv(index=False).encode(),file_name='leakhunter_savings_ledger.csv',mime='text/csv')
    else: st.info('Save a scan to initialize the ledger.')


with tabs[9]:
    st.markdown('### 💼 ROI & Pilot Center')
    st.write('Turn analytical findings into a finance-ready business case for a first customer pilot.')
    left,right=st.columns(2)
    with left:
        st.markdown('#### Savings assumptions')
        annual_spend=st.number_input('Annual procurement spend', min_value=0.0, value=float(invoice_spend*12), step=100000.0, format='%.0f')
        target_leakage_pct=st.number_input('Target leakage / savings %', min_value=0.0, max_value=20.0, value=1.5, step=0.1)
        realization_pct=st.number_input('Expected realization %', min_value=0.0, max_value=100.0, value=55.0, step=5.0)
        success_fee_pct=st.number_input('Example success fee %', min_value=0.0, max_value=50.0, value=15.0, step=1.0)
        pilot_months=st.number_input('Pilot duration (months)', min_value=1, max_value=12, value=2, step=1)
    gross=annual_spend*(target_leakage_pct/100)
    realized=gross*(realization_pct/100)
    fee=realized*(success_fee_pct/100)
    net=realized-fee
    with right:
        st.markdown('#### Finance view')
        a,b=st.columns(2); a.metric('Estimated opportunity', money(gross,currency)); b.metric('Expected realized savings', money(realized,currency))
        c,d=st.columns(2); c.metric('Example success fee', money(fee,currency)); d.metric('Customer net benefit', money(net,currency))
        st.success(f'Illustrative pilot ROI: customer receives about {money(net,currency)} of net value before internal costs, using the assumptions on the left.')
    st.markdown('#### Recommended pilot structure')
    pilot_rows=[
        {'Phase':'1. Data intake','Duration':'1–3 days','Output':'Invoices + POs + receipts + contracts mapped','Commercial objective':'Fast time-to-value'},
        {'Phase':'2. Discovery','Duration':f'{pilot_months} months','Output':'Leakage findings + evidence + supplier heatmap','Commercial objective':'Validate savings'},
        {'Phase':'3. Recovery','Duration':'Ongoing','Output':'Negotiation packs + savings ledger','Commercial objective':'Convert potential → realized'},
    ]
    st.dataframe(pd.DataFrame(pilot_rows),width='stretch',hide_index=True)
    st.markdown('#### Buyer message')
    buyer_msg = (f"LeakHunter AI analyzes procurement records to identify recoverable value across pricing, quantities, duplicate invoices, contract terms and supplier patterns.\n\n"
                 f"Illustrative annual spend: {annual_spend:,.0f} {currency}\n"
                 f"Estimated opportunity: {gross:,.0f} {currency}\n"
                 f"Expected realized savings: {realized:,.0f} {currency}\n"
                 f"Pilot: {pilot_months} months\n")
    st.text_area('Pilot one-pager copy',buyer_msg,height=180)
    st.download_button('Download pilot business case',buyer_msg.encode(),file_name='leakhunter_pilot_business_case.txt',mime='text/plain')
    st.markdown('#### Commercial positioning')
    st.info('Do not sell “AI”. Sell measurable value recovery: evidence-backed savings opportunities, negotiation actions, and a persistent realized-savings ledger.')


with tabs[10]:
    st.markdown('### 📑 Executive Savings Report')
    st.write('A finance-ready PDF summarizing spend, evidence-backed opportunity and the illustrative pilot case.')
    annual_spend_report=st.number_input('Annual spend for executive view',min_value=0.0,value=float(invoice_spend*12),step=100000.0,format='%.0f',key='report_annual_spend')
    target_report=st.number_input('Target savings %',min_value=0.0,max_value=20.0,value=1.5,step=0.1,key='report_target')
    realization_report=st.number_input('Realization %',min_value=0.0,max_value=100.0,value=55.0,step=5.0,key='report_realization')
    fee_report=st.number_input('Success fee %',min_value=0.0,max_value=50.0,value=15.0,step=1.0,key='report_fee')
    pilot_report=st.number_input('Pilot months',min_value=1,max_value=12,value=2,step=1,key='report_months')
    report_bytes=build_executive_report(workspace,currency,invoice_spend,potential,high_conf,duplicate_value,rebate_opp,len(scores),opportunities,scores,annual_spend_report,target_report,realization_report,fee_report,pilot_report)
    st.download_button('⬇️ Download Executive Savings Report (PDF)',report_bytes,file_name='leakhunter_executive_savings_report.pdf',mime='application/pdf',width='stretch')
    estimated=annual_spend_report*(target_report/100); realized=estimated*(realization_report/100); fee=realized*(fee_report/100); net=realized-fee
    a,b,c,d=st.columns(4); a.metric('Annual opportunity',money(estimated,currency)); b.metric('Expected realized',money(realized,currency)); c.metric('Example fee',money(fee,currency)); d.metric('Customer net benefit',money(net,currency))
    st.markdown('#### Executive takeaway')
    st.success(f'LeakHunter identifies {money(potential,currency)} of current opportunity and models {money(net,currency)} of illustrative annual net benefit under the assumptions above.')

with tabs[11]:
    if user['role']!='admin': st.warning('Admin role required for audit and workspace administration.')
    else:
        st.markdown('### Admin & Audit')
        st.markdown('#### Audit Trail'); logs=audit_df(org['id']); st.dataframe(logs,width='stretch',hide_index=True) if not logs.empty else st.info('No audit events yet.')
        st.markdown('#### Users'); conn=db(); users_df=pd.read_sql_query('SELECT id,email,display_name,role,created_at FROM users WHERE org_id=? ORDER BY id',conn,params=(org['id'],)); conn.close(); st.dataframe(users_df,width='stretch',hide_index=True)
        st.markdown('#### Data model'); st.code('Organization → Users → Scans → Opportunities → Savings Ledger → Audit Trail',language='text')

st.divider(); st.subheader('📦 CSV templates')
templates={'Invoices':['invoice_id','supplier_id','po_id','sku','category','uom','quantity','unit_price','currency','invoice_date'],'Purchase Orders':['po_id','supplier_id','sku','category','uom','ordered_qty','agreed_unit_price','currency','supplier_status'],'Goods Receipts':['receipt_id','po_id','supplier_id','sku','received_qty','receipt_date'],'Contracts':['contract_id','supplier_id','sku','contract_unit_price','rebate_pct','late_penalty_pct','payment_days','start_date','end_date','auto_renew','minimum_volume'],'Suppliers':['supplier_id','supplier_name','country','declared_risk','cyber_reviewed','financial_score','quality_score','delivery_score','compliance_score']}
cols=st.columns(len(templates))
for col,(label,headers) in zip(cols,templates.items()): col.download_button(label=f'{label} template',data=pd.DataFrame(columns=headers).to_csv(index=False).encode(),file_name=f'{label.lower().replace(" ","_")}_template.csv',mime='text/csv')
st.caption('LeakHunter AI v1.0 is a commercial demo prototype. Local SQLite and demo credentials are for development only. Production should use managed Postgres, SSO/OIDC, MFA, encrypted secrets, role-based access control, tenant isolation, and immutable audit logging.')
