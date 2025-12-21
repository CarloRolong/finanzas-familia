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
import plotly.graph_objects as go

# Configuración de Matplotlib para no usar ventana (Backend seguro)
plt.switch_backend('Agg')

# --- 1. CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Controle Financeiro", layout="wide")

# --- 2. SISTEMA DE SEGURIDAD (Blindado) ---
if not os.path.exists("credentials.json"):
    try:
        if "credenciales_seguras" in st.secrets:
            decoded = base64.b64decode(st.secrets["credenciales_seguras"])
            with open("credentials.json", "wb") as f:
                f.write(decoded)
        else:
            st.error("⚠️ No encontré el secreto 'credenciales_seguras'.")
    except Exception as e:
        st.error(f"Error creando credenciales: {e}")

# --- 3. INICIO DEL CÓDIGO DEL BOT ---
# Leemos el token desde los secretos de Streamlit
TOKEN = st.secrets["TOKEN_TELEGRAM"]

LISTA_BANCOS = ["Nubank", "Inter", "BB", "Bradesco"]
LISTA_CATEGORIAS = ["Alimentação", "Transporte", "Lazer", "Casa", "Serviços", "Saúde", "Educação", "Pets", "Outros"]
LISTA_PERSONAS = ["Carlos", "Jessy"]
TARJETAS_CONFIG = {"nubank": 4, "bb": 2, "inter": 6, "bradesco": 18}
COLOR_MAP_BANCOS = {'nubank': '#820AD1', 'bb': '#FFE600', 'inter': '#FF7A00', 'bradesco': '#CC092F', 'xp': '#000000', 'itau': '#EC7000', 'santander': '#EC0000'}
COLOR_DEFAULT = '#808080'
NOMBRE_HOJA = "Finanzas_Familia"
TAB_REGISTROS = "Registros"
TAB_PRESUPUESTO = "Orcamento"

datos_temporales = {}

# Funciones Auxiliares del Bot
def conectar_sheet(nombre_tab):
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
    nombre_banco = nombre_banco.lower()
    dia_corte = TARJETAS_CONFIG.get(nombre_banco, 1)
    if fecha_compra.day > dia_corte:
        return fecha_compra + relativedelta(months=1)
    return fecha_compra

# --- LÓGICA DEL BOT (Handlers) ---
bot = telebot.TeleBot(TOKEN)

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
    bot.send_message(chat_id, "Salvando no Google Sheets...")
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
                datetime.now().strftime("%d/%m/%Y"), fecha.strftime("%m-%Y"), quien,
                "Credito" if cuotas > 1 else "Debito", banco, monto_cuota,
                cuotas, i + 1, cat, f"{cat} ({i+1}/{cuotas})"
            ]
            filas.append(fila)

        sh = conectar_sheet(TAB_REGISTROS)
        for f in filas: sh.append_row(f)
        
        msg = f"✅ *Gasto Salvo*\n💲 R$ {monto:,.2f}\n🏦 {banco} - {quien}\n🏷️ {cat}"
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Novo Gasto", callback_data="menu_gasto"))
        bot.send_message(chat_id, "Mais alguma coisa?", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Erro salvando: {e}")

def generar_reporte(message):
    # (Tu lógica de reporte original se mantiene aquí, resumida para el ejemplo)
    try:
        sh_regs = conectar_sheet(TAB_REGISTROS)
        df_r = pd.DataFrame(sh_regs.get_all_records())
        
        if df_r.empty:
            bot.send_message(message.chat.id, "Sem dados.")
            return

        # Limpieza rápida
        if 'Valor' in df_r.columns: df_r['Valor'] = df_r['Valor'].apply(limpiar_numero)
        
        # Generar gráfico simple
        grupos = df_r.groupby(['Banco']).sum(numeric_only=True).reset_index()
        
        if grupos.empty:
            bot.send_message(message.chat.id, "Sem valores pendentes.")
        else:
            bot.send_message(message.chat.id, "📊 Gerando gráfico...")
            # Aquí va tu lógica de Matplotlib del reporte
            # ... (código del reporte)
            
            # Para prueba rápida:
            msg_resumo = f"Total registrado: R$ {df_r['Valor'].sum():,.2f}"
            bot.send_message(message.chat.id, msg_resumo)

    except Exception as e:
        bot.send_message(message.chat.id, f"Erro no relatório: {e}")

# --- 4. LANZADOR DEL BOT EN SEGUNDO PLANO ---
def iniciar_bot():
    print("🤖 Intentando iniciar el bot en segundo plano...")
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"Error en el bot: {e}")

# Esto asegura que el bot se ejecute en un hilo separado para no bloquear Streamlit
if not any(t.name == "ThreadBotTelegram" for t in threading.enumerate()):
    t = threading.Thread(target=iniciar_bot, name="ThreadBotTelegram", daemon=True)
    t.start()
    st.toast("🤖 Bot de Telegram iniciado en la nube!")

# ==========================================
# --- 5. TU DASHBOARD WEB (STREAMLIT) ---
# ==========================================
st.title("💰 Controle Financeiro da Família")
st.write("El sistema está activo. Usa el Bot de Telegram para registrar gastos.")

try:
    # Conectamos para mostrar datos en la web
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sh = client.open(NOMBRE_HOJA).worksheet(TAB_REGISTROS)
    
    data = sh.get_all_records()
    df = pd.DataFrame(data)
    
    if not df.empty:
        # Unos KPIs rápidos
        st.metric("Total Registros", len(df))
        if 'Valor' in df.columns:
            # Limpieza simple para visualización web
            df['Valor'] = df['Valor'].apply(limpiar_numero)
            total = df['Valor'].sum()
            st.metric("Gasto Total Acumulado", f"R$ {total:,.2f}")
            
        st.dataframe(df)
    else:
        st.warning("La hoja de cálculo está vacía.")

except Exception as e:
    st.error(f"Error cargando datos web: {e}")


