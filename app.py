import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from dateutil.relativedelta import relativedelta
import plotly.express as px
import plotly.graph_objects as go

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Controle Financeiro", layout="wide")

st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 24px; }
</style>
""", unsafe_allow_html=True)

# Cores
COLOR_MAP_BANCOS = {
    'nubank': '#820AD1', 'bb': '#FFE600', 'inter': '#FF7A00', 'bradesco': '#CC092F',
    'xp': '#000000', 'itau': '#EC7000', 'santander': '#EC0000'
}
COLOR_DEFAULT = '#808080'

TARJETAS_CONFIG = {
    "nubank": 4, "bb": 2, "inter": 6, "bradesco": 18, "xp": 15
}

def limpiar_numero(valor):
    if isinstance(valor, (int, float)): return float(valor)
    valor_str = str(valor).strip().replace('R$', '').strip()
    if not valor_str: return 0.0
    if ',' in valor_str:
        valor_str = valor_str.replace('.', '').replace(',', '.')
    try: return float(valor_str)
    except: return 0.0

# --- CONEXÃO ---
@st.cache_data(ttl=5)
def cargar_datos():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sheet = client.open("Finanzas_Familia")
    
    try:
        vals_r = sheet.worksheet("Registros").get_all_records()
        df_r = pd.DataFrame(vals_r)
    except: return pd.DataFrame(), pd.DataFrame()

    try:
        vals_p = sheet.worksheet("Orcamento").get_all_records()
        df_p = pd.DataFrame(vals_p)
    except: return pd.DataFrame(), pd.DataFrame()
    
    # Estandarizar nombres
    mapa_cols = {
        'Monto': 'Valor', 'Monto_Total': 'Valor', 
        'Quien': 'Quem', 'Persona': 'Quem',
        'Descripcion': 'Descricao', 'Descripción': 'Descricao',
        'Categoria': 'Categoria', 'Mes_Ref': 'Mes_Ref', 'Banco': 'Banco', 'Limite': 'Limite'
    }
    df_r.rename(columns=mapa_cols, inplace=True)
    df_p.rename(columns=mapa_cols, inplace=True)

    # Limpieza
    if not df_r.empty:
        if 'Valor' in df_r.columns: df_r['Valor'] = df_r['Valor'].apply(limpiar_numero)
        for c in ['Mes_Ref', 'Banco', 'Quem', 'Categoria']:
            if c in df_r.columns: df_r[c] = df_r[c].astype(str).str.strip()

    if not df_p.empty:
        if 'Limite' in df_p.columns: df_p['Limite'] = df_p['Limite'].apply(limpiar_numero)
        if 'Categoria' in df_p.columns: df_p['Categoria'] = df_p['Categoria'].astype(str).str.strip()
        
    return df_r, df_p

# --- INÍCIO APP ---
st.title("💰 Controle Financeiro Inteligente")

try:
    df_gastos, df_limites = cargar_datos()
    if df_gastos.empty:
        st.warning("Aguardando dados...")
        st.stop()

    # ==========================================
    # 🔴 ZONA 1: FATURAS ABERTAS (REAL TIME / AO VIVO)
    # ==========================================
    # Esta zona NO obedece al filtro de mes. Obedece a la fecha de HOY.
    
    st.subheader("💳 Faturas em Aberto (Status Atual)")
    
    if 'Quem' not in df_gastos.columns: df_gastos['Quem'] = 'Geral'
    
    # Lista para guardar los datos vivos para el gráfico de torta
    datos_live_pie = [] 

    grupos = df_gastos.groupby(['Banco', 'Quem']).size().reset_index()
    cols = st.columns(4)
    col_idx = 0
    hoy = datetime.now()
    
    for _, row in grupos.iterrows():
        banco_real = row['Banco']
        quien_real = row['Quem']
        banco_key = str(banco_real).lower().strip()
        
        # Calcular mes activo HOY
        dia_corte = TARJETAS_CONFIG.get(banco_key, 1)
        if hoy.day > dia_corte:
            fecha_fatura = hoy + relativedelta(months=1) # Ya pasó el corte, es mes siguiente
        else:
            fecha_fatura = hoy # Aún no pasa el corte, es este mes
            
        mes_fatura_abierta = fecha_fatura.strftime("%m-%Y")
        
        # Filtro de factura abierta
        filtro = (df_gastos['Banco'] == banco_real) & \
                 (df_gastos['Quem'] == quien_real) & \
                 (df_gastos['Mes_Ref'] == mes_fatura_abierta)
        
        total = df_gastos[filtro]['Valor'].sum()
        
        # Guardamos este dato para el gráfico de torta
        if total > 0:
            datos_live_pie.append({'Banco': banco_real, 'Valor': total})
        
        # Mostrar Tarjeta
        total_str = f"R$ {total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        vencimiento = f"{dia_corte}/{fecha_fatura.strftime('%m')}"
        
        with cols[col_idx % 4]:
            st.metric(f"{banco_real} - {quien_real}", total_str, f"Fatura: {vencimiento}", delta_color="off")
        col_idx += 1
    
    st.divider()

    # ==========================================
    # 🔵 ZONA 2: ANÁLISE E PLANEJAMENTO (FILTRADO)
    # ==========================================
    # Esta zona SÍ obedece al filtro de mes.
    
    st.sidebar.header("🔍 Filtros de Análise")
    meses = sorted(df_gastos['Mes_Ref'].unique())
    mes_actual = hoy.strftime("%m-%Y")
    meses.sort(key=lambda x: datetime.strptime(x, "%m-%Y") if x else datetime.now())
    idx = meses.index(mes_actual) if mes_actual in meses else 0
    mes_sel = st.sidebar.selectbox("Selecionar Mês:", meses, index=idx)
    
    df_mes = df_gastos[df_gastos['Mes_Ref'] == mes_sel].copy()
    
    # --- KPI's DEL MES SELECCIONADO ---
    total_gasto_mes = df_mes['Valor'].sum() if not df_mes.empty else 0
    total_orcamento = df_limites['Limite'].sum() if not df_limites.empty else 0
    saldo = total_orcamento - total_gasto_mes
    
    k1, k2, k3 = st.columns(3)
    k1.metric(f"Total a Pagar ({mes_sel})", f"R$ {total_gasto_mes:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
    k2.metric("Orçamento Planejado", f"R$ {total_orcamento:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
    k3.metric("Saldo Disponível", f"R$ {saldo:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'), delta_color="normal" if saldo >=0 else "inverse")

    st.markdown(f"#### 🗓️ Detalhes de: {mes_sel}")

    # --- GRÁFICOS ---
    c1, c2 = st.columns([2, 1])
    
    with c1:
        st.subheader("📊 Gastos vs Orçamento (Mês Selecionado)")
        if not df_mes.empty:
            gastos_cat = df_mes.groupby('Categoria')['Valor'].sum().reset_index()
            df_final = pd.merge(df_limites, gastos_cat, on="Categoria", how="outer").fillna(0)
            df_final['Restante'] = df_final['Limite'] - df_final['Valor']
            df_final['Restante_Visual'] = df_final['Restante'].apply(lambda x: max(x, 0))
            df_final = df_final.sort_values(by=['Limite', 'Valor'])

            fig = px.bar(df_final, y='Categoria', x=['Valor', 'Restante_Visual'], orientation='h',
                         color_discrete_map={'Valor': '#ff4b4b', 'Restante_Visual': '#3dd56d'}, height=500)
            
            new_names = {'Valor': 'Gasto', 'Restante_Visual': 'Disponível'}
            fig.for_each_trace(lambda t: t.update(name=new_names.get(t.name, t.name)))
            st.plotly_chart(fig, use_container_width=True)
            df_sem_show = df_final.copy()
        else:
            st.info("Sem dados neste mês.")
            df_sem_show = pd.DataFrame()

    with c2:
        # AQUÍ USAMOS LOS DATOS "LIVE" (DE ARRIBA) PARA EL GRÁFICO DE TORTA
        st.subheader("💳 Faturas Abertas (Ao Vivo)")
        if datos_live_pie:
            df_pie = pd.DataFrame(datos_live_pie)
            # Agrupar por Banco (sumando si hay varias personas en el mismo banco)
            df_pie_show = df_pie.groupby('Banco')['Valor'].sum().reset_index()
            
            colores = [COLOR_MAP_BANCOS.get(b.lower(), COLOR_DEFAULT) for b in df_pie_show['Banco']]
            
            fig = go.Figure(data=[go.Pie(
                labels=df_pie_show['Banco'], 
                values=df_pie_show['Valor'], 
                texttemplate='R$ %{value:,.0f}', 
                marker=dict(colors=colores),
                hole=0.4
            )])
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Tudo pago! Nenhuma fatura aberta hoje.")

    # --- TABLAS ---
    c3, c4 = st.columns(2)
    with c3:
        st.subheader("🚦 Semáforo (Mês Selecionado)")
        if not df_sem_show.empty:
            tbl = df_sem_show[['Categoria', 'Limite', 'Valor', 'Restante']].copy()
            for c in ['Limite', 'Valor', 'Restante']: tbl[c] = pd.to_numeric(tbl[c], errors='coerce').fillna(0.0)
            tbl = tbl.sort_values(by='Valor', ascending=False)
            tbl.columns = ['Categoria', 'Limite', 'Gasto', 'Restante']
            st.dataframe(tbl.style.format({
                'Limite': "R$ {:,.2f}", 'Gasto': "R$ {:,.2f}", 'Restante': "R$ {:,.2f}"
            }), use_container_width=True)

    with c4:
        st.subheader("📝 Extrato (Mês Selecionado)")
        cols_ver = ['Data', 'Descricao', 'Categoria', 'Banco', 'Quem', 'Valor']
        if not df_mes.empty:
            cols_ok = [c for c in cols_ver if c in df_mes.columns]
            df_show = df_mes[cols_ok].copy()
            if 'Valor' in df_show.columns: df_show['Valor'] = pd.to_numeric(df_show['Valor'], errors='coerce').fillna(0.0)
            st.dataframe(df_show.style.format({'Valor': "R$ {:,.2f}"}), use_container_width=True)

except Exception as e:
    st.error(f"Erro: {e}")