import time
import datetime
import pytz
from threading import Thread
import serial
from dotenv import load_dotenv
import os
import requests

load_dotenv()  # Carrega .env do diretório do script de borda

# Configurações do Servidor de Borda
ARDUINO_PORT = os.getenv("ARDUINO_PORT", '/dev/ttyACM0')  # Pega do .env ou usa default
BAUD_RATE = 9600
CLOUD_API_LEITURAS_SNAPSHOT = os.getenv("CLOUD_API_ENDPOINT_LEITURAS") # Para o snapshot que vai pro MongoDB
CLOUD_API_LEITURAS_LIVE = os.getenv("CLOUD_API_ENDPOINT_LIVE_UPDATE") # NOVO: Para atualizações ao vivo para o cliente
CLOUD_API_COMANDOS = os.getenv("CLOUD_API_ENDPOINT_COMANDOS")
DEVICE_ID = os.getenv("DEVICE_ID", "minhaEstufa01")

print(f"--- Configurações servidor_borda.py ---")
print(f"ARDUINO_PORT: {ARDUINO_PORT}")
print(f"CLOUD_API_ENDPOINT_LEITURAS (Snapshot/MongoDB): {CLOUD_API_LEITURAS_SNAPSHOT}")
print(f"CLOUD_API_ENDPOINT_LIVE_UPDATE (Cliente): {CLOUD_API_LEITURAS_LIVE}") # NOVO
print(f"CLOUD_API_ENDPOINT_COMANDOS: {CLOUD_API_COMANDOS}")
print(f"DEVICE_ID: {DEVICE_ID}")
print(f"-------------------------------------")

# Tempo #
br_tz = pytz.timezone("America/Sao_Paulo")
timestamp_local = datetime.datetime.now(br_tz).isoformat()

# Conexão com Arduino (pode precisar de try-except se a porta não estiver sempre disponível)
try:
    arduino = serial.Serial(ARDUINO_PORT, BAUD_RATE, timeout=1)
    print(f"Conectado ao Arduino em {ARDUINO_PORT}")
    time.sleep(2)  # Aguarda a serial estabilizar
except serial.SerialException as e:
    print(f"Erro ao conectar com Arduino em {ARDUINO_PORT}: {e}")
    arduino = None  # Define arduino como None se a conexão falhar

# --- Variáveis Globais da Borda ---
limiteTemp = 30
limiteLuz = 700
inversorUmi = 0  # 0: irrigar quando seco (umidade=1), 1: irrigar quando molhado (umidade=0)

# Contadores de acionamento desde o último envio para a nuvem
irrigadorSwitch_count = 0
lampadaSwitch_count = 0
aquecedorSwitch_count = 0
refrigeradorSwitch_count = 0

# Estado ATUAL dos atuadores (ON/OFF) - controlado pelo piloto automático ou comandos
estado_atuadores = {
    'estadoIrrigador': 'OFF',
    'estadoLampada': 'OFF',
    'estadoAquecedor': 'OFF',
    'estadoRefrigerador': 'OFF',
    'estadoPilotoAutomatico': 'OFF'  # Será controlado por comando da nuvem ou localmente
}
auto_mode = False  # Estado do piloto automático

command_buffer = []  # Buffer de comandos recebidos da nuvem

# Últimas leituras brutas dos sensores (atualizado por publish_sensor_data)
sensor_data = {
    'readLuminosidade': None,
    'readUmidade': None,
    'readTemperatura': None
}

# Variáveis para lógica de filtragem de dados em publish_sensor_data
last_processed_luminosidade = None
last_processed_umidade = None
last_processed_temperatura = None
first_reading_processed = False


def enviar_leitura_para_nuvem_snapshot(luminosidade, umidade, temperatura, atuadores_contagem):
    if not CLOUD_API_LEITURAS_SNAPSHOT:
        print("URL da API para SNAPSHOT (CLOUD_API_ENDPOINT_LEITURAS) não configurada.")
        return

    payload = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "luminosidade": luminosidade,
        "umidade": umidade,
        "temperatura": temperatura,
        "irrigador_times_on": atuadores_contagem.get("irrigador", 0),
        "lampada_times_on": atuadores_contagem.get("lampada", 0),
        "aquecedor_times_on": atuadores_contagem.get("aquecedor", 0),
        "refrigerador_times_on": atuadores_contagem.get("refrigerador", 0)
    }
    try:
        response = requests.post(CLOUD_API_LEITURAS_SNAPSHOT, json=payload, timeout=10)
        response.raise_for_status()
        print(f"SNAPSHOT enviado para MongoDB via nuvem ({CLOUD_API_LEITURAS_SNAPSHOT}): {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar SNAPSHOT para nuvem ({CLOUD_API_LEITURAS_SNAPSHOT}): {e}")

# NOVA função para enviar leituras para o cliente em "tempo real"
def enviar_leitura_live_para_nuvem(luminosidade, umidade, temperatura, estado_atual_atuadores):
    if not CLOUD_API_LEITURAS_LIVE:
        print("URL da API para LIVE UPDATE (CLOUD_API_ENDPOINT_LIVE_UPDATE) não configurada.")
        return

    payload = {
        "device_id": DEVICE_ID,
        "timestamp": timestamp_local,
        "luminosidade": luminosidade,
        "umidade": umidade,
        "temperatura": temperatura,
        "estado_atuadores": estado_atual_atuadores # Envia o dicionário completo de estados ON/OFF
    }
    try:
        response = requests.post(CLOUD_API_LEITURAS_LIVE, json=payload, timeout=5) # Timeout menor para live
        response.raise_for_status()
        # Não precisa de print aqui para não poluir o log, a nuvem confirmará o SSE
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar LEITURA LIVE para nuvem ({CLOUD_API_LEITURAS_LIVE}): {e}")


# Esta função (snapshot periódico) continua como está, mas chama a função renomeada
def enviar_snapshot_para_nuvem():
    global irrigadorSwitch_count, lampadaSwitch_count, aquecedorSwitch_count, refrigeradorSwitch_count
    while True:
        time.sleep(300) # Mantém o envio periódico para o MongoDB
        # ... (lógica para pegar temp, umi, lum de sensor_data) ...
        try:
            temp_raw_ts = sensor_data.get('readTemperatura')
            umi_raw_ts = sensor_data.get('readUmidade')
            lum_raw_ts = sensor_data.get('readLuminosidade')

            if temp_raw_ts and umi_raw_ts and lum_raw_ts:
                temperatura = float(temp_raw_ts.split('-')[0])
                umidade = int(umi_raw_ts.split('-')[0])
                luminosidade = float(lum_raw_ts.split('-')[0])

                atuadores_contagem_atual = {
                    "irrigador": irrigadorSwitch_count, "lampada": lampadaSwitch_count,
                    "aquecedor": aquecedorSwitch_count, "refrigerador": refrigeradorSwitch_count
                }
                enviar_leitura_para_nuvem_snapshot(luminosidade, umidade, temperatura, atuadores_contagem_atual)

                irrigadorSwitch_count = 0; lampadaSwitch_count = 0; aquecedorSwitch_count = 0; refrigeradorSwitch_count = 0
            else:
                print("Dados dos sensores incompletos para envio de SNAPSHOT à nuvem.")
        except Exception as e:
            print(f"Erro na thread de enviar_snapshot_para_nuvem: {e}")


def buscar_comandos_da_nuvem():
    if not CLOUD_API_COMANDOS:
        print("URL da API para comandos (CLOUD_API_ENDPOINT_COMANDOS) não configurada.")
        return None
    try:
        params = {'device_id': DEVICE_ID}
        response = requests.get(CLOUD_API_COMANDOS, params=params, timeout=5)
        response.raise_for_status()
        comandos = response.json()
        if comandos:  # A API deve retornar uma lista de strings de comando
            print(f"Comandos recebidos da nuvem: {comandos}")
            return comandos
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar comandos da nuvem ({CLOUD_API_COMANDOS}): {e}")
    except ValueError:  # requests.JSONDecodeError herda de ValueError
        print(
            f"Erro ao decodificar JSON da resposta de comandos. Conteúdo: {response.text if 'response' in locals() else 'N/A'}")
    return None


def command_poller_thread():
    while True:
        novos_comandos = buscar_comandos_da_nuvem()
        if novos_comandos and isinstance(novos_comandos, list):
            for cmd_item in novos_comandos:  # Renomeado para cmd_item para clareza
                if isinstance(cmd_item, str):
                    print(f"Adicionando comando de string ao buffer: {cmd_item}")
                    command_buffer.append(cmd_item)
                elif isinstance(cmd_item, dict) and 'command' in cmd_item:
                    comando_principal = cmd_item['command']
                    if comando_principal == 'set_auto_mode':
                        global auto_mode  # Já é global, mas explícito aqui
                        auto_mode = cmd_item.get('value', False)
                        estado_atuadores['estadoPilotoAutomatico'] = 'ON' if auto_mode else 'OFF'
                        print(f"Piloto automático (borda) definido para: {auto_mode}")
                    else:
                        # Se for outro comando em formato de dicionário que DEVE ir pro Arduino
                        print(f"Adicionando comando de dict ao buffer: {comando_principal}")
                        command_buffer.append(comando_principal)
        time.sleep(10)


# --- Lógica do Arduino e Atuadores ---
def publish_sensor_data():
    global last_processed_luminosidade, last_processed_umidade, last_processed_temperatura
    global first_reading_processed, sensor_data, estado_atuadores  # Adicionado estado_atuadores aqui

    if not arduino:
        print("Arduino não conectado. Thread publish_sensor_data não pode iniciar.")
        return

    while True:
        if arduino.in_waiting > 0:
            try:
                # ... (sua lógica de ler do Arduino e parsear os dados, igual) ...
                linha = arduino.readline().decode('utf-8', errors='ignore').strip()
                if not linha: time.sleep(0.005); continue
                partes = linha.split(';')
                dados_arduino = {}
                for parte in partes:
                    if ':' in parte:
                        key_value = parte.split(':', 1)
                        if len(key_value) == 2: dados_arduino[key_value[0].strip()] = key_value[1].strip()
                current_luminosidade_str = dados_arduino.get("LDR")
                current_umidade_str = dados_arduino.get("UMIDADE")
                current_temperatura_str = dados_arduino.get("TEMPERATURA")
                if not (current_luminosidade_str and current_umidade_str and current_temperatura_str):
                    time.sleep(0.005);
                    continue
                current_luminosidade = float(current_luminosidade_str)
                current_umidade = int(current_umidade_str)
                current_temperatura = float(current_temperatura_str)

                timestamp_obj = datetime.datetime.now()
                timestamp_str = timestamp_obj.strftime('%H:%M:%S')

                sensor_data['readLuminosidade'] = f'{current_luminosidade:.2f}-{timestamp_str}'
                sensor_data['readUmidade'] = f'{current_umidade}-{timestamp_str}'
                sensor_data['readTemperatura'] = f'{current_temperatura:.2f}-{timestamp_str}'

                process_this_reading = False
                # ... (sua lógica de filtragem para process_this_reading, igual) ...
                if not first_reading_processed:
                    process_this_reading = True
                else:
                    if last_processed_luminosidade is not None:
                        if abs(last_processed_luminosidade) < 1e-6:
                            if abs(current_luminosidade) > 1e-6: process_this_reading = True
                        elif (abs(current_luminosidade - last_processed_luminosidade) / abs(
                            last_processed_luminosidade)) * 100 > 2.0:
                            process_this_reading = True
                    if not process_this_reading and last_processed_umidade is not None:
                        if current_umidade != last_processed_umidade: process_this_reading = True
                    if not process_this_reading and last_processed_temperatura is not None:
                        if abs(last_processed_temperatura) < 1e-6:
                            if abs(current_temperatura) > 1e-6: process_this_reading = True
                        elif (abs(current_temperatura - last_processed_temperatura) / abs(
                            last_processed_temperatura)) * 100 > 2.0:
                            process_this_reading = True

                if process_this_reading:
                    umidadetexto = 'Molhado' if current_umidade == 0 else 'Seco'
                    print(
                        f"Leitura SIGNIFICATIVA ({timestamp_str}): Lum={current_luminosidade:.2f}, Umi={umidadetexto}({current_umidade}), Temp={current_temperatura:.2f}°C. ENVIANDO PARA STREAM...")

                    # Envia para o NOVO endpoint de "live update"
                    # Passa uma cópia do estado_atuadores para evitar problemas com threads se ele for modificado enquanto é enviado
                    enviar_leitura_live_para_nuvem(current_luminosidade, current_umidade, current_temperatura,
                                                   dict(estado_atuadores))

                    last_processed_luminosidade = current_luminosidade
                    last_processed_umidade = current_umidade
                    last_processed_temperatura = current_temperatura
                    if not first_reading_processed: first_reading_processed = True

            # ... (seus blocos except) ...
            except Exception as e:
                print(f"Erro em publish_sensor_data: {e}. Linha: '{linha if 'linha' in locals() else 'N/A'}'")
        time.sleep(0.005)


def process_command_buffer():
    global limiteTemp, limiteLuz
    if not arduino:
        print("Arduino não conectado. Thread process_command_buffer não pode operar.")
        return
    while True:
        if command_buffer:
            command_str = command_buffer.pop(0)
            print(f"Processando comando do buffer: {command_str}")
            try:
                # Atualização de limites diretamente:
                if command_str.startswith("set_limiteTemp_"):
                    try:
                        novo_temp = float(command_str.split('_')[-1])
                        if 10 <= novo_temp <= 50:
                            limiteTemp = novo_temp
                            print(f"Limite de Temperatura atualizado na borda: {limiteTemp}°C")
                        else:
                            print(f"Valor inválido para limiteTemp: {novo_temp}")
                    except ValueError:
                        print(f"Comando mal formatado: {command_str}")
                    continue  # Não enviar para Arduino

                elif command_str.startswith("set_limiteLuz_"):
                    try:
                        novo_luz = float(command_str.split('_')[-1])
                        if 100 <= novo_luz <= 1000:
                            limiteLuz = novo_luz
                            print(f"Limite de Luminosidade atualizado na borda: {limiteLuz} Lux")
                        else:
                            print(f"Valor inválido para limiteLuz: {novo_luz}")
                    except ValueError:
                        print(f"Comando mal formatado: {command_str}")
                    continue  # Não enviar para Arduino

                # Outros comandos → enviar para Arduino
                arduino.write((command_str + '\n').encode('utf-8'))
                print(f"Comando '{command_str}\\n' enviado para Arduino.")

                # Atualiza estado_atuadores se for ON/OFF
                partes_comando = command_str.split('_')
                if len(partes_comando) == 2:
                    atuador_nome_cmd = partes_comando[0].replace("toggle", "")
                    atuador_estado_cmd = partes_comando[1]
                    mapa_atuadores = {
                        "Irrigador": "estadoIrrigador",
                        "Lampada": "estadoLampada",
                        "Aquecedor": "estadoAquecedor",
                        "Refrigerador": "estadoRefrigerador"
                    }
                    if atuador_nome_cmd in mapa_atuadores:
                        chave_estado = mapa_atuadores[atuador_nome_cmd]
                        if estado_atuadores[chave_estado] != atuador_estado_cmd:
                            estado_atuadores[chave_estado] = atuador_estado_cmd
                            print(f"Estado local de {chave_estado} atualizado para {atuador_estado_cmd}")

            except Exception as e:
                print(f"Erro ao processar comando '{command_str}': {e}")

            time.sleep(3)
        else:
            time.sleep(0.5)


def piloto_automatico():
    global auto_mode, irrigadorSwitch_count, lampadaSwitch_count, aquecedorSwitch_count, refrigeradorSwitch_count

    print(f"Piloto automático iniciado. Modo atual: {'ATIVO' if auto_mode else 'INATIVO'}")
    if estado_atuadores['estadoPilotoAutomatico'] == 'OFF' and auto_mode:  # Sincroniza display
        estado_atuadores['estadoPilotoAutomatico'] = 'ON'

    while True:
        if auto_mode:
            try:
                temp_data_ts = sensor_data.get('readTemperatura')
                umi_data_ts = sensor_data.get('readUmidade')  # Espera 0 (molhado) ou 1 (seco)
                lum_data_ts = sensor_data.get('readLuminosidade')

                if temp_data_ts and umi_data_ts and lum_data_ts:
                    temp = float(temp_data_ts.split('-')[0])
                    umi = int(umi_data_ts.split('-')[0])  # 0 ou 1
                    lum = float(lum_data_ts.split('-')[0])

                    # Lógica Refrigerador
                    if temp >= limiteTemp and estado_atuadores['estadoRefrigerador'] == 'OFF':
                        command_buffer.append('toggleRefrigerador_ON')
                        # estado_atuadores['estadoRefrigerador'] = 'ON' # Será atualizado por process_command_buffer
                        refrigeradorSwitch_count += 1
                    elif temp < limiteTemp and estado_atuadores['estadoRefrigerador'] == 'ON':
                        command_buffer.append('toggleRefrigerador_OFF')
                        # estado_atuadores['estadoRefrigerador'] = 'OFF'

                    # Lógica Aquecedor (inverso do refrigerador, não devem ligar juntos)
                    if temp < (limiteTemp - 5) and estado_atuadores['estadoAquecedor'] == 'OFF' and estado_atuadores[
                        'estadoRefrigerador'] == 'OFF':  # Ex: Ligar aquecedor se temp < 25
                        command_buffer.append('toggleAquecedor_ON')
                        # estado_atuadores['estadoAquecedor'] = 'ON'
                        aquecedorSwitch_count += 1
                    elif temp >= (limiteTemp - 5) and estado_atuadores['estadoAquecedor'] == 'ON':
                        command_buffer.append('toggleAquecedor_OFF')
                        # estado_atuadores['estadoAquecedor'] = 'OFF'

                    # Lógica Irrigador:
                    # inversorUmi = 0: UMI=1 (seco) -> Ligar Irrigador. UMI=0 (molhado) -> Desligar.
                    # inversorUmi = 1: UMI=0 (molhado) -> Ligar Irrigador. UMI=1 (seco) -> Desligar.
                    deve_irrigar = (inversorUmi == 0 and umi == 1) or \
                                   (inversorUmi == 1 and umi == 0)

                    if deve_irrigar and estado_atuadores['estadoIrrigador'] == 'OFF':
                        command_buffer.append('toggleIrrigador_ON')
                        # estado_atuadores['estadoIrrigador'] = 'ON'
                        irrigadorSwitch_count += 1
                    elif not deve_irrigar and estado_atuadores['estadoIrrigador'] == 'ON':
                        command_buffer.append('toggleIrrigador_OFF')
                        # estado_atuadores['estadoIrrigador'] = 'OFF'

                    # Lógica Lâmpada
                    if lum < limiteLuz and estado_atuadores['estadoLampada'] == 'OFF':  # Alterado para < limiteLuz
                        command_buffer.append('toggleLampada_ON')
                        # estado_atuadores['estadoLampada'] = 'ON'
                        lampadaSwitch_count += 1
                    elif lum >= limiteLuz and estado_atuadores['estadoLampada'] == 'ON':  # Alterado para >= limiteLuz
                        command_buffer.append('toggleLampada_OFF')
                        # estado_atuadores['estadoLampada'] = 'OFF'
                else:
                    print("Piloto Automático: Dados dos sensores não disponíveis ou incompletos.")
            except Exception as e:
                print(f"Erro no piloto automático: {e}")
        else:  # Se auto_mode for False, garantir que o estadoPilotoAutomatico reflita isso
            if estado_atuadores['estadoPilotoAutomatico'] == 'ON':
                estado_atuadores['estadoPilotoAutomatico'] = 'OFF'
                print("Piloto automático DESATIVADO.")

        time.sleep(5)  # Intervalo de checagem do piloto automático


if __name__ == '__main__':
    if not arduino:
        print("Script de borda encerrando pois o Arduino não está conectado.")
        exit()

    print("Servidor de borda iniciado...")
    # auto_mode é False por padrão. Pode ser alterado por comando da nuvem.
    # print(f"Piloto automático inicialmente: {'ATIVO' if auto_mode else 'INATIVO'}")

    # Inicializa as threads
    Thread(target=publish_sensor_data, daemon=True).start()
    Thread(target=piloto_automatico, daemon=True).start()
    Thread(target=process_command_buffer, daemon=True).start()
    Thread(target=command_poller_thread, daemon=True).start()
    Thread(target=enviar_snapshot_para_nuvem,
           daemon=True).start()  # ADICIONADO DE VOLTA (nome antigo era snapshot_to_mongo)

    try:
        while True:
            # Printar estado atual periodicamente para debug
            # print(f"Debug Estado: AutoMode={auto_mode}, Atuadores={estado_atuadores}, Buffer Comandos={len(command_buffer)}")
            time.sleep(60)
    except KeyboardInterrupt:
        print("Encerrando servidor de borda...")
    finally:
        if arduino and arduino.is_open:
            arduino.close()
            print("Porta serial do Arduino fechada.")