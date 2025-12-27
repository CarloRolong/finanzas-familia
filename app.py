import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from dateutil.relativedelta import relativedelta
import plotly.express as px
import plotly.graph_objects as go
import base64
import os
import threading
import time
import telebot
from telebot import types
import matplotlib.pyplot as plt

# ==============================================================================
# 1. CONFIGURACIÓN Y ESTILOS
# ==============================================================================
st.set_page_config(page_title="Controle Financeiro", layout="wide", page_icon="💰")
plt.switch_backend('Agg') # Configuración segura para el bot

st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 24px; }
</style>
""", unsafe_allow_html=True)

# --- GLOBALES (Compartidas entre Bot y Web) ---
COLOR_MAP_BANCOS = {
    'nubank': '#820AD1', 'bb': '#FFE600', 'inter': '#FF7A00', 'bradesco': "#CC092F",
    'pix': '#00BFA5' # <--- AGREGADO: Color Verde para PIX
}
COLOR_DEFAULT = '#808080'

TARJETAS_CONFIG = {
    "nubank": 4, "bb": 2, "inter": 6, "bradesco": 20
}

LISTA_BANCOS = ["Nubank", "Inter", "BB", "Bradesco"]
LISTA_CATEGORIAS = ["Alimentação", "Transporte", "Lazer", "Casa", "Serviços", "Saúde", "Educação", "Pets", "Outros"]
LISTA_PERSONAS = ["Carlos", "Jessy"]

# ==============================================================================
# 2. SEGURIDAD BLINDADA (CREACIÓN DE CREDENCIALES)
# ==============================================================================
if not os.path.exists("credentials.json"):
    try:
        if "credenciales_seguras" in st.secrets:
            decoded = base64.b64decode(st.secrets["credenciales_seguras"])
            with open("credentials.json", "wb") as f:
                f.write(decoded)
        else:
            st.error("⚠️ Error: No encontré 'credenciales_seguras' en los Secrets.")
            st.stop()
    except Exception as e:
        st.error(f"Error crítico creando credenciales: {e}")
        st.stop()

# ==============================================================================
# 3. CÓDIGO DEL BOT DE TELEGRAM (EJECUCIÓN EN FONDO)
# ==============================================================================
try:
    TOKEN = st.secrets["TOKEN_TELEGRAM"]
except:
    st.warning("⚠️ Falta el TOKEN_TELEGRAM en los Secrets.")
    TOKEN = "TOKEN_DUMMY"

bot = telebot.TeleBot(TOKEN)
datos_temporales = {}
NOMBRE_HOJA = "Finanzas_Familia"
TAB_REGISTROS = "Registros"

# --- Funciones Auxiliares del Bot ---
def conectar_sheet_bot(nombre_tab):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(NOMBRE_HOJA).worksheet(nombre_tab)

def limpiar_numero_bot(valor):
    if isinstance(valor, (int, float)): return float(valor)
    valor_str = str(valor).strip().replace('R$', '').strip()
    if not valor_str: return 0.0
    if ',' in valor_str: valor_str = valor_str.replace('.', '').replace(',', '.')
    try: return float(valor_str)
    except: return 0.0

def calcular_primer_mes_pago_bot(fecha_compra, nombre_banco):
    nombre_banco = str(nombre_banco).lower().strip()
    
    # <--- AGREGADO: Lógica para PIX (Pago inmediato, fecha de hoy)
    if nombre_banco == 'pix':
        return fecha_compra
    # -------------------------------------------------------------

    dia_corte = TARJETAS_CONFIG.get(nombre_banco, 1)
    if fecha_compra.day > dia_corte:
        return fecha_compra + relativedelta(months=1)
    return fecha_compra

# --- Menús del Bot ---
@bot.message_handler(commands=['start', 'help'])
@bot.message_handler(func=lambda m: m.text.lower() in ['oi', 'hola', 'olá'])
def menu_principal(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("Registrar Gasto", callback_data="menu_gasto"),
               types.InlineKeyboardButton("Ver Relatório", callback_data="menu_reporte"))
    bot.reply_to(message, "Olá! O que vamos fazer?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    
    if call.data == "menu_reporte":
        bot.answer_callback_query(call.id, "Gerando...")
        generar_reporte_bot(call.message)
        
    elif call.data == "menu_gasto":
        datos_temporales[chat_id] = {}
        msg = bot.send_message(chat_id, "Digite o Valor (Ex: 50,00):")
        bot.register_next_step_handler(msg, paso_recibir_monto)
        
    elif call.data.startswith("tipo_"):
        tipo = call.data.split("_")[1]
        
        # <--- AGREGADO: Manejo de PIX
        if tipo == 'pix':
            datos_temporales[chat_id]['tipo'] = 'pix'
            datos_temporales[chat_id]['cuotas'] = 1
            datos_temporales[chat_id]['banco'] = 'PIX'
            mostrar_menu_personas(chat_id) # Saltamos banco, vamos a Persona
        # -----------------------------
        elif tipo == 'parcelado':
            datos_temporales[chat_id]['tipo'] = 'parcelado'
            msg = bot.send_message(chat_id, "Quantas Parcelas?")
            bot.register_next_step_handler(msg, paso_recibir_cuotas)
        else: # Avista Credito
            datos_temporales[chat_id]['tipo'] = 'avista'
            datos_temporales[chat_id]['cuotas'] = 1
            mostrar_menu_bancos(chat_id)
            
    elif call.data.startswith("banco_"):
        datos_temporales[chat_id]['banco'] = call.data.split("_")[1]
        mostrar_menu_personas(chat_id)
        
    elif call.data.startswith("quien_"):
        datos_temporales[chat_id]['quien'] = call.data.split("_")[1]
        mostrar_menu_categorias(chat_id)
        
    elif call.data.startswith("cat_"):
        cat = call.data.split("_")[1]
        if cat == "Outros":
            msg = bot.send_message(chat_id, "O que é especificamente?")
            bot.register_next_step_handler(msg, lambda m: [datos_temporales[chat_id].update({'categoria': m.text.title()}), guardar_gasto_final(chat_id)])
        else:
            datos_temporales[chat_id]['categoria'] = cat
            guardar_gasto_final(chat_id)

def paso_recibir_monto(message):
    try:
        monto = limpiar_numero_bot(message.text)
        if monto == 0: raise ValueError
        datos_temporales[message.chat.id]['monto'] = monto
        
        markup = types.InlineKeyboardMarkup()
        # <--- AGREGADO: Botón PIX
        markup.add(types.InlineKeyboardButton("💠 PIX", callback_data="tipo_pix")) 
        markup.add(types.InlineKeyboardButton("💳 Crédito À Vista", callback_data="tipo_avista"),
                   types.InlineKeyboardButton("📅 Parcelado", callback_data="tipo_parcelado"))
                   
        bot.send_message(message.chat.id, f"✅ R$ {monto:,.2f}\nComo vai pagar?", reply_markup=markup)
    except:
        msg = bot.reply_to(message, "❌ Valor inválido.")
        bot.register_next_step_handler(msg, paso_recibir_monto)

def paso_recibir_cuotas(message):
    try:
        cuotas = int(message.text)
        datos_temporales[message.chat.id]['cuotas'] = cuotas
        mostrar_menu_bancos(message.chat.id)
    except:
        msg = bot.reply_to(message, "❌ Use apenas números.")
        bot.register_next_step_handler(msg, paso_recibir_cuotas)

def mostrar_menu_bancos(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(b, callback_data=f"banco_{b}") for b in LISTA_BANCOS])
    bot.send_message(chat_id, "Qual Banco?", reply_markup=markup)

def mostrar_menu_personas(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(p, callback_data=f"quien_{p}") for p in LISTA_PERSONAS])
    bot.send_message(chat_id, "Quem pagou?", reply_markup=markup)

def mostrar_menu_categorias(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(*[types.InlineKeyboardButton(c, callback_data=f"cat_{c}") for c in LISTA_CATEGORIAS])
    bot.send_message(chat_id, "Qual a Categoria?", reply_markup=markup)

def guardar_gasto_final(chat_id):
    datos = datos_temporales.get(chat_id)
    bot.send_message(chat_id, "Salvando...")
    try:
        monto = datos['monto']
        cuotas = datos['cuotas']
        banco = datos['banco']
        quien = datos['quien']
        cat = datos['categoria']
        
        monto_cuota = round(monto / cuotas, 2)
        fecha_inicio = calcular_primer_mes_pago_bot(datetime.now(), banco)
        
        filas = []
        for i in range(cuotas):
            fecha = fecha_inicio + relativedelta(months=i)
            
            # <--- AGREGADO: Definir Tipo de Gasto
            if banco == 'PIX':
                tipo_reg = "Debito" # PIX cuenta como débito/efectivo
            else:
                tipo_reg = "Credito" if cuotas > 1 else "Debito"
            # -----------------------------------

            # ORDEN COLUMNAS: Data, Mes_Ref, Quem, Tipo, Banco, Valor, Parc, Parc_Atual, Cat, Desc
            fila = [
                datetime.now().strftime("%d/%m/%Y"),
                fecha.strftime("%m-%Y"),
                quien,
                tipo_reg,
                banco,
                monto_cuota,
                cuotas,
                i + 1,
                cat,
                f"{cat} ({i+1}/{cuotas})"
            ]
            filas.append(fila)

        sh = conectar_sheet_bot(TAB_REGISTROS)
        for f in filas: sh.append_row(f)
        
        # <--- AGREGADO: Icono dinámico
        icono_banco = "💠" if banco == 'PIX' else "🏦"
        msg = f"✅ *Salvo*\n💲 R$ {monto:,.2f}\n{icono_banco} {banco} - {quien}\n🏷️ {cat}"
        
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Novo Gasto", callback_data="menu_gasto"))
        bot.send_message(chat_id, "Mais alguma coisa?", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Erro: {e}")

def generar_reporte_bot(message):
    # Reporte rápido texto para el bot
    try:
        sh_regs = conectar_sheet_bot(TAB_REGISTROS)
        df_r = pd.DataFrame(sh_regs.get_all_records())
        if df_r.empty:
            bot.send_message(message.chat.id, "Sem dados.")
            return
        if 'Valor' in df_r.columns:
            df_r['Valor'] = df_r['Valor'].apply(limpiar_numero_bot)
            total = df_r['Valor'].sum()
            bot.send_message(message.chat.id, f"📊 Total acumulado no sistema: R$ {total:,.2f}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Erro: {e}")

# --- INICIADOR HILO (THREAD) ---
def iniciar_bot():
    try:
        print("🤖 Bot iniciado...")
        bot.infinity_polling()
    except:
        pass

if not any(t.name == "ThreadBotTelegram" for t in threading.enumerate()):
    t = threading.Thread(target=iniciar_bot, name="ThreadBotTelegram", daemon=True)
    t.start()

# ==============================================================================
# 4. TU DASHBOARD WEB ORIGINAL (VISUALIZACIÓN)
# ==============================================================================

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
        st.warning("Aguardando dados... (Use o Bot para registrar)")
        st.stop()

    # ==========================================
    # 🔴 ZONA 1: FATURAS ABERTAS (REAL TIME / AO VIVO)
    # ==========================================
    
    st.subheader("💳 Faturas em Aberto (Status Atual)")
    
    if 'Quem' not in df_gastos.columns: df_gastos['Quem'] = 'Geral'
    
    datos_live_pie = [] 

    grupos = df_gastos.groupby(['Banco', 'Quem']).size().reset_index()
    cols = st.columns(4)
    col_idx = 0
    hoy = datetime.now()
    
    for _, row in grupos.iterrows():
        banco_real = row['Banco']
        quien_real = row['Quem']
        banco_key = str(banco_real).lower().strip()
        
        # <--- AGREGADO: Lógica Visual para PIX (Es Hoy)
        if banco_key == 'pix':
            fecha_fatura = hoy
        # ----------------------------------------------
        else:
            # Calcular mes activo HOY (Tarjetas)
            dia_corte = TARJETAS_CONFIG.get(banco_key, 1)
            if hoy.day > dia_corte:
                fecha_fatura = hoy + relativedelta(months=1)
            else:
                fecha_fatura = hoy
            
        mes_fatura_abierta = fecha_fatura.strftime("%m-%Y")
        
        filtro = (df_gastos['Banco'] == banco_real) & \
                 (df_gastos['Quem'] == quien_real) & \
                 (df_gastos['Mes_Ref'] == mes_fatura_abierta)
        
        total = df_gastos[filtro]['Valor'].sum()
        
        if total > 0:
            datos_live_pie.append({'Banco': banco_real, 'Valor': total})
        
        # Mostrar Tarjeta (Ocultamos PIX de las tarjetas físicas, pero lo dejamos en el gráfico)
        if banco_key != 'pix': 
            total_str = f"R$ {total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            vencimiento = f"{dia_corte}/{fecha_fatura.strftime('%m')}"
            
            with cols[col_idx % 4]:
                st.metric(f"{banco_real} - {quien_real}", total_str, f"Fatura: {vencimiento}", delta_color="off")
            col_idx += 1
    
    st.divider()

    # ==========================================
    # 🔵 ZONA 2: ANÁLISE E PLANEJAMENTO (FILTRADO)
    # ==========================================
    
    st.sidebar.header("🔍 Filtros de Análise")
    meses = sorted(df_gastos['Mes_Ref'].unique())
    mes_actual = hoy.strftime("%m-%Y")
    # Ordenar meses cronológicamente
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
        st.subheader("💳 Faturas Abertas (Ao Vivo)")
        if datos_live_pie:
            df_pie = pd.DataFrame(datos_live_pie)
            df_pie_show = df_pie.groupby('Banco')['Valor'].sum().reset_index()
            
            # Asignar colores correctos
            colores = [COLOR_MAP_BANCOS.get(str(b).lower(), COLOR_DEFAULT) for b in df_pie_show['Banco']]
            
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
        st.subheader("Orçamento vs Gastos (Mês Selecionado)")
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

