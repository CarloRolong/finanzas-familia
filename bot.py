import telebot
from telebot import types
import gspread
import pandas as pd
import matplotlib.pyplot as plt
import io
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Configuración de Matplotlib para no usar ventana
plt.switch_backend('Agg')

# --- CONFIGURAÇÃO ---
TOKEN = "8263550806:AAE4xDoPuDIRNWeJCu8i1gFVC3k0M1Ua7_s" 

LISTA_BANCOS = ["Nubank", "Inter", "BB", "Bradesco"]
LISTA_CATEGORIAS = ["Alimentação", "Transporte", "Lazer", "Casa", "Serviços", "Saúde", "Educação", "Pets", "Outros"]
LISTA_PERSONAS = ["Carlos", "Jessy"]

TARJETAS_CONFIG = {"nubank": 4, "bb": 2, "inter": 6, "bradesco": 18}

# Colores Oficiales (Igual al Dashboard)
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

NOMBRE_HOJA = "Finanzas_Familia"
TAB_REGISTROS = "Registros"
TAB_PRESUPUESTO = "Orcamento"

scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
client = gspread.authorize(creds)
bot = telebot.TeleBot(TOKEN)
datos_temporales = {}

def limpiar_numero(valor):
    if isinstance(valor, (int, float)): return float(valor)
    valor_str = str(valor).strip().replace('R$', '').strip()
    if not valor_str: return 0.0
    if ',' in valor_str: valor_str = valor_str.replace('.', '').replace(',', '.')
    try: return float(valor_str)
    except: return 0.0

def conectar_sheet(nombre_tab):
    client = gspread.authorize(creds)
    return client.open(NOMBRE_HOJA).worksheet(nombre_tab)

def calcular_primer_mes_pago(fecha_compra, nombre_banco):
    nombre_banco = nombre_banco.lower()
    dia_corte = TARJETAS_CONFIG.get(nombre_banco, 1)
    if fecha_compra.day > dia_corte:
        return fecha_compra + relativedelta(months=1)
    return fecha_compra

# --- MENUS ---
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
            # ORDEN: Data, Mes_Ref, Quem, Tipo, Banco, Valor, Parc, Parc_Atual, Cat, Desc
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

        sh = conectar_sheet(TAB_REGISTROS)
        for f in filas: sh.append_row(f)
        
        msg = f" *Salvo*\n R$ {monto:,.2f}\n {banco} - {quien}\n {cat}"
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Novo", callback_data="menu_gasto"))
        bot.send_message(chat_id, "Mais alguma coisa?", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Erro: {e}")

# --- REPORTE MEJORADO (PIZZA DARK MODE) ---
def generar_reporte(message):
    try:
        sh_regs = conectar_sheet(TAB_REGISTROS)
        sh_pres = conectar_sheet(TAB_PRESUPUESTO)
        
        df_r = pd.DataFrame(sh_regs.get_all_records())
        df_p = pd.DataFrame(sh_pres.get_all_records())
        
        if df_r.empty:
            bot.send_message(message.chat.id, "Sem dados.")
            return

        # Limpieza básica
        if 'Valor' in df_r.columns: df_r['Valor'] = df_r['Valor'].apply(limpiar_numero)
        if 'Limite' in df_p.columns: df_p['Limite'] = df_p['Limite'].apply(limpiar_numero)
        if 'Mes_Ref' in df_r.columns: df_r['Mes_Ref'] = df_r['Mes_Ref'].astype(str).str.strip()
        if 'Quem' in df_r.columns: df_r['Quem'] = df_r['Quem'].astype(str).str.strip()
        if 'Banco' in df_r.columns: df_r['Banco'] = df_r['Banco'].astype(str).str.strip()

        # ---------------------------------------------------------
        # 1. CÁLCULO DE FATURAS ABERTAS (Lógica "Live" del Dashboard)
        # ---------------------------------------------------------
        
        # Agrupamos por Banco + Persona
        grupos = df_r.groupby(['Banco', 'Quem']).size().reset_index()
        hoy = datetime.now()
        
        data_pie = {} # Diccionario para guardar { "Nubank - Carlos": 150.00 }
        
        for _, row in grupos.iterrows():
            banco = row['Banco']
            quien = row['Quem']
            banco_key = str(banco).lower().strip()
            
            # Calcular Mes Activo para este banco hoy
            dia_corte = TARJETAS_CONFIG.get(banco_key, 1)
            if hoy.day > dia_corte:
                fecha_fatura = hoy + relativedelta(months=1)
            else:
                fecha_fatura = hoy
            mes_fatura = fecha_fatura.strftime("%m-%Y")
            
            # Sumar lo que hay en esa factura
            filtro = (df_r['Banco'] == banco) & (df_r['Quem'] == quien) & (df_r['Mes_Ref'] == mes_fatura)
            total = df_r[filtro]['Valor'].sum()
            
            if total > 0:
                label_completo = f"{banco} ({quien})"
                data_pie[label_completo] = total
        
        # ---------------------------------------------------------
        # 2. GENERAR GRÁFICO ESTILO DASHBOARD (DARK)
        # ---------------------------------------------------------
        if not data_pie:
            bot.send_message(message.chat.id, "Tudo pago! Nenhuma fatura aberta hoje.")
        else:
            labels = list(data_pie.keys())
            valores = list(data_pie.values())
            
            # Asignar colores (buscando la palabra clave del banco en el label)
            colores = []
            for lbl in labels:
                color_encontrado = COLOR_DEFAULT
                for b_key, b_color in COLOR_MAP_BANCOS.items():
                    if b_key in lbl.lower():
                        color_encontrado = b_color
                        break
                colores.append(color_encontrado)
            
            # Configuración Dark Mode
            plt.style.use('dark_background') # Fondo oscuro global
            fig, ax = plt.subplots(figsize=(6, 6))
            fig.patch.set_facecolor('#0E1117') # Fondo exacto de Streamlit
            ax.set_facecolor('#0E1117')
            
            # Función para mostrar R$ real en vez de %
            def func_fmt(pct, allvals):
                absolute = int(round(pct/100.*sum(allvals)))
                return f"R$ {absolute}"

            wedges, texts, autotexts = ax.pie(
                valores, 
                labels=labels, 
                autopct=lambda pct: func_fmt(pct, valores),
                startangle=140, 
                colors=colores,
                textprops={'color':"w"} # Texto blanco
            )
            
            plt.setp(autotexts, size=10, weight="bold")
            plt.title("💳 Faturas Abertas (Ao Vivo)", color='white', fontsize=14)
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', facecolor='#0E1117')
            buf.seek(0)
            plt.close()
            bot.send_photo(message.chat.id, photo=buf)

        # ---------------------------------------------------------
        # 3. LISTA DE PRESUPUESTO (Mes Actual Calendario)
        # ---------------------------------------------------------
        mes_cal = hoy.strftime("%m-%Y")
        df_mes_cal = df_r[df_r['Mes_Ref'] == mes_cal]
        
        gastos_cat = df_mes_cal.groupby('Categoria')['Valor'].sum().reset_index()
        df_final = pd.merge(df_p, gastos_cat, on="Categoria", how="outer").fillna(0)
        
        txt = f"📊 *ORÇAMENTO ({mes_cal})*\n"
        txt += "-------------------------\n"
        for _, row in df_final.iterrows():
            cat = row['Categoria']
            lim = row['Limite']
            gasto = row['Valor']
            
            # Icono
            icono = "🟢"
            if lim > 0:
                if (gasto/lim) > 0.8: icono = "🟠"
                if (gasto/lim) >= 1.0: icono = "🔴"
            elif gasto > 0: # Si no tiene limite pero tiene gasto
                icono = "⚠️" 
                
            if gasto > 0 or lim > 0:
                txt += f"{icono} *{cat}*\n"
                txt += f"   Gasto: R$ {gasto:,.2f} / {lim:,.0f}\n"
        
        bot.send_message(message.chat.id, txt, parse_mode="Markdown")

    except Exception as e:
        bot.send_message(message.chat.id, f"Erro no relatório: {e}")

print("🤖 BOT INICIADO...")
bot.infinity_polling()