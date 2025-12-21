import streamlit as st
import pandas as pd
import gspread
import os
import base64
import threading
import time
import telebot
from telebot import types
import matplotlib.pyplot as plt
import io
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from dateutil.relativedelta import relativedelta
import plotly.express as px

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL Y SEGURIDAD
# ==============================================================================
st.set_page_config(page_title="Controle Financeiro", layout="wide", page_icon="💰")

# Configuración de Matplotlib para evitar errores de servidor
plt.switch_backend('Agg')

# --- RECUPERACIÓN DE CREDENCIALES (SISTEMA BLINDADO) ---
if not os.path.exists("credentials.json"):
    try:
        if "credenciales_seguras" in st.secrets:
            decoded = base64.b64decode(st.secrets["credenciales_seguras"])
            with open("credentials.json", "wb") as f:
                f.write(decoded)
        else:
            st.error("⚠️ Error Crítico: No encontré el secreto 'credenciales_seguras'.")
            st.stop()
    except Exception as e:
        st.error(f"Error creando credenciales: {e}")
        st.stop()

# ==============================================================================
# 2. CEREBRO DEL BOT DE TELEGRAM (CÓDIGO COMPLETO)
# ==============================================================================

# --- Variables Globales y Configuración ---
TOKEN = st.secrets["TOKEN_TELEGRAM"]
LISTA_BANCOS = ["Nubank", "Inter", "BB", "Bradesco"]
LISTA_CATEGORIAS = ["Alimentação", "Transporte", "Lazer", "Casa", "Serviços", "Saúde", "Educação", "Pets", "Outros"]
LISTA_PERSONAS = ["Carlos", "Jessy"]
TARJETAS_CONFIG = {"nubank": 4, "bb": 2, "inter": 6, "bradesco": 18}
NOMBRE_HOJA = "Finanzas_Familia"
TAB_REGISTROS = "Registros"
TAB_PRESUPUESTO = "Orcamento"

# Colores Oficiales
COLOR_MAP_BANCOS = {
    'nubank': '#820AD1', 
    'bb': '#FFE600', 
    'inter': '#FF7A00', 
    'bradesco': '#CC092F',
    'xp': '#000000',
    'itau': '#EC7000',
    'santander': '#EC0000'
}
COLOR_DEFAULT = '#808080'

bot = telebot.TeleBot(TOKEN)
datos_temporales = {}

# --- Funciones Auxiliares ---
def conectar_sheet_bot(nombre_tab):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(NOMBRE_HOJA).worksheet(nombre_tab)

def limpiar_numero(valor):
    if isinstance(valor, (int, float)): return float(valor)
    valor_str = str(valor).strip().replace('R$', '').strip()
    if not valor_str: return 0.0
    if ',' in valor_str: valor_str = valor_str.replace('.', '').replace(',', '.')
    try: return float(valor_str)
    except: return 0.0

def calcular_primer_mes_pago(fecha_compra, nombre_banco):
    nombre_banco = str(nombre_banco).lower().strip()
    dia_corte = TARJETAS_CONFIG.get(nombre_banco, 1)
    if fecha_compra.day > dia_corte:
        return fecha_compra + relativedelta(months=1)
    return fecha_compra

# --- Lógica de Menús y Handlers del Bot ---

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
        generar_reporte(call.message)
    elif call.data == "menu_gasto":
        datos_temporales[chat_id] = {}
        msg = bot.send_message(chat_id, "Digite o Valor (Ex: 50,00):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, paso_recibir_monto)
    elif call.data.startswith("tipo_"):
        tipo = call.data.split("_")[1]
        datos_temporales[chat_id]['tipo'] = tipo
        if tipo == 'parcelado':
            msg = bot.send_message(chat_id, "Quantas Parcelas?", parse_mode="Markdown")
            bot.register_next_step_handler(msg, paso_recibir_cuotas)
        else:
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
        monto = limpiar_numero(message.text)
        if monto == 0: raise ValueError
        datos_temporales[message.chat.id]['monto'] = monto
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("À Vista", callback_data="tipo_avista"),
                   types.InlineKeyboardButton("Parcelado", callback_data="tipo_parcelado"))
        bot.send_message(message.chat.id, f"✅ R$ {monto:,.2f}\nComo vai pagar?", reply_markup=markup)
    except:
        msg = bot.reply_to(message, "❌ Valor inválido. Tente novamente:")
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
    bot.send_message(chat_id, "De quem é o cartão?", reply_markup=markup)

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
        fecha_inicio = calcular_primer_mes_pago(datetime.now(), banco)
        
        filas = []
        for i in range(cuotas):
            fecha = fecha_inicio + relativedelta(months=i)
            fila = [
                datetime.now().strftime("%d/%m/%Y"),
                fecha.strftime("%m-%Y"),
                quien,
                "Credito" if cuotas > 1 else "Debito",
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
        
        msg = f"✅ *Salvo*\n💲 R$ {monto:,.2f}\n🏦 {banco} - {quien}\n🏷️ {cat}"
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Novo", callback_data="menu_gasto"))
        bot.send_message(chat_id, "Mais alguma coisa?", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Erro: {e}")

def generar_reporte(message):
    try:
        sh_regs = conectar_sheet_bot(TAB_REGISTROS)
        df_r = pd.DataFrame(sh_regs.get_all_records())
        
        if df_r.empty:
            bot.send_message(message.chat.id, "Sem dados.")
            return

        # Limpieza básica
        if 'Valor' in df_r.columns: df_r['Valor'] = df_r['Valor'].apply(limpiar_numero)
        if 'Mes_Ref' in df_r.columns: df_r['Mes_Ref'] = df_r['Mes_Ref'].astype(str).str.strip()

        # Cálculo de Faturas Abertas
        grupos = df_r.groupby(['Banco', 'Quem']).size().reset_index()
        hoy = datetime.now()
        data_pie = {}
        
        for _, row in grupos.iterrows():
            banco = row['Banco']
            quien = row['Quem']
            banco_key = str(banco).lower().strip()
            
            dia_corte = TARJETAS_CONFIG.get(banco_key, 1)
            if hoy.day > dia_corte:
                fecha_fatura = hoy + relativedelta(months=1)
            else:
                fecha_fatura = hoy
            mes_fatura = fecha_fatura.strftime("%m-%Y")
            
            filtro = (df_r['Banco'] == banco) & (df_r['Quem'] == quien) & (df_r['Mes_Ref'] == mes_fatura)
            total = df_r[filtro]['Valor'].sum()
            
            if total > 0:
                label_completo = f"{banco} ({quien})"
                data_pie[label_completo] = total
        
        if not data_pie:
            bot.send_message(message.chat.id, "Tudo pago! Nenhuma fatura aberta hoje.")
        else:
            labels = list(data_pie.keys())
            valores = list(data_pie.values())
            colores = []
            for lbl in labels:
                color_encontrado = COLOR_DEFAULT
                for b_key, b_color in COLOR_MAP_BANCOS.items():
                    if b_key in lbl.lower():
                        color_encontrado = b_color
                        break
                colores.append(color_encontrado)
            
            # Gráfico Dark Mode
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(6, 6))
            fig.patch.set_facecolor('#0E1117')
            ax.set_facecolor('#0E1117')
            
            def func_fmt(pct, allvals):
                absolute = int(round(pct/100.*sum(allvals)))
                return f"R$ {absolute}"

            wedges, texts, autotexts = ax.pie(
                valores, labels=labels, autopct=lambda pct: func_fmt(pct, valores),
                startangle=140, colors=colores, textprops={'color':"w"}
            )
            plt.setp(autotexts, size=10, weight="bold")
            plt.title("💳 Faturas Abertas (Ao Vivo)", color='white', fontsize=14)
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', facecolor='#0E1117')
            buf.seek(0)
            plt.close()
            bot.send_photo(message.chat.id, photo=buf)
            
    except Exception as e:
        bot.send_message(message.chat.id, f"Erro no relatório: {e}")

# --- INICIADOR DE HILO (THREAD) PARA EL BOT ---
def iniciar_bot():
    print("🤖 Bot iniciado en segundo plano...")
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"Error bot: {e}")

if not any(t.name == "ThreadBotTelegram" for t in threading.enumerate()):
    t = threading.Thread(target=iniciar_bot, name="ThreadBotTelegram", daemon=True)
    t.start()

# ==============================================================================
# 3. DASHBOARD WEB (VISUALIZACIÓN EN STREAMLIT)
# ==============================================================================

def load_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sh = client.open(NOMBRE_HOJA).worksheet(TAB_REGISTROS)
    data = sh.get_all_records()
    df = pd.DataFrame(data)
    if not df.empty and 'Valor' in df.columns:
        df['Valor'] = df['Valor'].apply(limpiar_numero)
        # Normalizar para colores
        df['Banco_Key'] = df['Banco'].str.lower().str.strip()
    return df

st.title("📊 Painel Financeiro da Família")

if st.button("🔄 Atualizar Dados"):
    st.cache_data.clear()

try:
    df = load_data()
    
    if df.empty:
        st.info("Sem dados registrados ainda. Use o Bot no Telegram!")
    else:
        # --- KPIS ---
        total = df['Valor'].sum()
        mes_actual = datetime.now().strftime("%m-%Y")
        gasto_mes = df[df['Mes_Ref'] == mes_actual]['Valor'].sum()
        
        col1, col2 = st.columns(2)
        col1.metric("Gasto Total Histórico", f"R$ {total:,.2f}")
        col2.metric(f"Gasto Mes Atual ({mes_actual})", f"R$ {gasto_mes:,.2f}")
        
        st.markdown("---")

        # --- GRÁFICOS ---
        c1, c2 = st.columns(2)
        
        # 1. Donut Chart (Bancos con Colores Reales)
        gastos_banco = df.groupby('Banco')['Valor'].sum().reset_index()
        # Mapeamos la columna 'Banco' a los colores usando el diccionario
        # Nota: Ajustamos las llaves para que coincidan (minúsculas)
        gastos_banco['Color'] = gastos_banco['Banco'].str.lower().str.strip().map(COLOR_MAP_BANCOS).fillna(COLOR_DEFAULT)
        
        fig_pie = px.pie(
            gastos_banco, 
            values='Valor', 
            names='Banco', 
            title='Distribuição por Banco',
            hole=0.4,
            color='Banco',
            # Este mapa forza los colores correctos si el nombre coincide con la llave
            color_discrete_map={k.title(): v for k, v in COLOR_MAP_BANCOS.items()} 
        )
        c1.plotly_chart(fig_pie, use_container_width=True)
        
        # 2. Bar Chart (Categorías)
        gastos_cat = df.groupby('Categoria')['Valor'].sum().reset_index().sort_values('Valor', ascending=False)
        fig_bar = px.bar(
            gastos_cat, 
            x='Categoria', 
            y='Valor', 
            title='Gastos por Categoria',
            text_auto='.2s',
            color='Categoria'
        )
        c2.plotly_chart(fig_bar, use_container_width=True)
        
        # --- TABLA ---
        st.subheader("📝 Últimos 10 Registros")
        st.dataframe(df.tail(10).sort_index(ascending=False), use_container_width=True)

except Exception as e:
    st.error(f"Erro carregando o painel: {e}")



