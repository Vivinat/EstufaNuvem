from flask import Flask, request, jsonify, render_template, Response
from pymongo.mongo_client import MongoClient
from pymongo import DESCENDING
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import os
import datetime
import time
import queue
import json
import pytz


load_dotenv()

app = Flask(__name__)

# Configurações
MONGO_URI = os.getenv("MONGO_URI_PROD")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY_PROD")
FROM_EMAIL = os.getenv("FROM_EMAIL_PROD")
TO_EMAIL = os.getenv("TO_EMAIL_PROD")

print(f"--- Configurações nuvem.py ---")
print(f"MONGO_URI_PROD: {MONGO_URI}")
print(f"SENDGRID_API_KEY_PROD: {'********' if SENDGRID_API_KEY else None}") # Não imprima a chave inteira
print(f"PORT: {os.getenv('PORT', 8080)}")
print(f"-----------------------------")
cache_ultimo_estado = None

# Validação das configurações essenciais
if not MONGO_URI:
    app.logger.error("MONGO_URI_PROD não configurado nas variáveis de ambiente.")
    # Você pode querer que o app não inicie se configurações críticas faltarem
if not SENDGRID_API_KEY:
    app.logger.warning("SENDGRID_API_KEY_PROD não configurado. Funcionalidade de email será afetada.")

try:
    client = MongoClient(MONGO_URI)
    db = client["EstufaBD"]
    colecao_leituras = db["LeiturasTable"]
    colecao_comandos = db["ComandosTable"]
    colecao_config = db["ConfigTable"]
    # Testar conexão
    client.admin.command('ping')
    app.logger.info("Conexão com MongoDB bem-sucedida.")
except Exception as e:
    app.logger.error(f"Erro ao conectar com MongoDB: {e}")
    client = None  # Impede operações no DB se a conexão falhar

# Fila para armazenar as atualizações ao vivo que serão enviadas via SSE
live_update_queue = queue.Queue()

# --- Endpoints para o Cliente ---
# --- ROTA PARA SERVIR A INTERFACE DO CLIENTE ---
@app.route('/')
def home():
    # Cria a coleção de comandos se não existir, para evitar erros na primeira vez
    if client and "ComandosTable" not in db.list_collection_names():
        try:
            db.create_collection("ComandosTable")
            app.logger.info("Coleção 'ComandosTable' criada.")
        except Exception as e:
            app.logger.error(f"Erro ao criar 'ComandosTable': {e}")
    return render_template('index.html') # Servirá o arquivo templates/index.html



# --- Endpoints para o Servidor de Borda ---
@app.route('/api/leituras', methods=['POST','GET'])
def receber_leituras():
    if not client:  # Verifica se a conexão com o DB está ativa
        return jsonify({"error": "Conexão com o banco de dados indisponível"}), 500
    data = request.json
    try:
        doc = {
            "timestamp": datetime.datetime.fromisoformat(data["timestamp"]),
            "luminosidade": float(data["luminosidade"]),
            "umidade": int(data["umidade"]),
            "temperatura": float(data["temperatura"]),
            "irrigador_times_on": int(data.get("irrigador_times_on", 0)),
            "lampada_times_on": int(data.get("lampada_times_on", 0)),
            "aquecedor_times_on": int(data.get("aquecedor_times_on", 0)),
            "refrigerador_times_on": int(data.get("refrigerador_times_on", 0)),
            "received_at": datetime.datetime.utcnow()
        }
        colecao_leituras.insert_one(doc)
        return jsonify({"message": "Leitura recebida com sucesso"}), 201
    except Exception as e:
        app.logger.error(f"Erro ao processar leitura: {e}")
        return jsonify({"error": str(e)}), 400

@app.route('/api/atualizar_limites', methods=['POST'])
def atualizar_limites():
    if not client:
        return jsonify({"error": "Conexão com o banco de dados indisponível"}), 500

    data = request.json
    device_id = data.get('device_id')
    limite_temp = data.get('limiteTemp')
    limite_luz = data.get('limiteLuz')

    if not device_id or limite_temp is None or limite_luz is None:
        return jsonify({"error": "Campos obrigatórios: device_id, limiteTemp, limiteLuz"}), 400

    if not (10 <= limite_temp <= 50) or not (100 <= limite_luz <= 1000):
        return jsonify({"error": "Valores inválidos. Temp: 10-50°C. Luz: 100-1000 Lux."}), 400

    try:
        colecao_config.insert_one({
            "limiteTemp": limite_temp,
            "limiteLuz": limite_luz,
            "atualizado_em": datetime.datetime.utcnow()
        })

        colecao_comandos.insert_many([
            {"device_id": device_id, "comando": f"set_limiteTemp_{limite_temp}", "status": "pendente", "created_at": datetime.datetime.utcnow()},
            {"device_id": device_id, "comando": f"set_limiteLuz_{limite_luz}", "status": "pendente", "created_at": datetime.datetime.utcnow()}
        ])

        return jsonify({"message": "Limites atualizados e comandos enviados para a borda."}), 200
    except Exception as e:
        app.logger.error(f"Erro ao atualizar limites: {e}")
        return jsonify({"error": "Erro ao atualizar limites"}), 500

@app.route('/api/limites_atuais', methods=['GET'])
def limites_atuais():
    if not client:
        return jsonify({"error": "Conexão com o banco de dados indisponível"}), 500
    try:
        ultimo = colecao_config.find_one(sort=[("atualizado_em", DESCENDING)])
        if ultimo:
            return jsonify({
                "limiteTemp": ultimo.get('limiteTemp', 20),
                "limiteLuz": ultimo.get('limiteLuz', 600)
            }), 200
        else:
            return jsonify({"limiteTemp": 20, "limiteLuz": 600}), 200
    except Exception as e:
        app.logger.error(f"Erro ao buscar limites: {e}")
        return jsonify({"error": "Erro ao buscar limites"}), 500


# NOVO Endpoint para receber ATUALIZAÇÕES AO VIVO da borda. ELE NAO MANDA PRO MONGO, SÓ PRO CLIENTE
@app.route('/api/live_update', methods=['POST'])
def receber_live_update():
    global cache_ultimo_estado
    data = request.json
    try:
        live_data_payload = {
            "device_id": data.get("device_id"),
            "timestamp": data.get("timestamp"),
            "luminosidade": data.get("luminosidade"),
            "umidade": data.get("umidade"),
            "temperatura": data.get("temperatura"),
            "estado_atuadores": data.get("estado_atuadores", {})
        }
        cache_ultimo_estado = live_data_payload  # Atualiza o cache
        live_update_queue.put(live_data_payload)
        return jsonify({"message": "Live update recebido"}), 200
    except Exception as e:
        app.logger.error(f"Erro ao processar live update: {e}")
        return jsonify({"error": "Erro ao processar live update"}), 400


# ROTA PRO CLIENTE QUE ENTROU AGORA NO APLICATIVO SABER O QUE ESTÁ LIGADO
@app.route('/api/estado_atual', methods=['GET'])
def fornecer_estado_atual():
    if cache_ultimo_estado:
        return jsonify(cache_ultimo_estado), 200
    else:
        return jsonify({"error": "Nenhum estado disponível ainda."}), 404

# Rota para o STREAM de Server-Sent Events (SSE)
@app.route('/stream')
def stream():
    def event_stream():
        try:
            while True:
                # Espera por um novo item na fila (bloqueante com timeout)
                try:
                    data_to_send = live_update_queue.get(timeout=1) # Espera 1s
                    # Formata como um evento SSE
                    # O cliente JS vai escutar por eventos do tipo 'live_leitura'
                    sse_event = f"event: live_leitura\ndata: {json.dumps(data_to_send)}\n\n"
                    yield sse_event
                    live_update_queue.task_done() # Indica que o item foi processado
                except queue.Empty:
                    # Se timeout, envia um comentário para manter a conexão viva (opcional)
                    yield ": keep-alive\n\n" # Comentário SSE
                time.sleep(0.1) # Pequeno delay para não sobrecarregar
        except GeneratorExit: # Cliente desconectou
            app.logger.info("Cliente SSE desconectado.")
        except Exception as e:
            app.logger.error(f"Erro no stream SSE: {e}")

    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/api/comandos', methods=['GET'])
def fornecer_comandos():
    device_id = request.args.get('device_id')
    if not device_id:
        return jsonify({"error": "device_id é obrigatório"}), 400

    comandos_para_enviar = []
    if client:
        try:
            # Pega até 5 comandos pendentes mais antigos para o device_id
            comandos_pendentes_cursor = colecao_comandos.find(
                {"device_id": device_id, "status": "pendente"}
            ).sort("created_at", 1).limit(5)  # 1 para ASCENDING (mais antigo primeiro)

            ids_para_atualizar = []
            for cmd_doc in comandos_pendentes_cursor:
                # Adiciona o comando string ou o dict de comando inteiro
                # Depende de como seu servidor de borda espera processá-lo.
                # O servidor de borda atual parece esperar uma string simples.
                if 'comando' in cmd_doc:
                    comandos_para_enviar.append(cmd_doc['comando'])
                ids_para_atualizar.append(cmd_doc['_id'])

            if ids_para_atualizar:
                colecao_comandos.update_many(
                    {"_id": {"$in": ids_para_atualizar}},
                    {"$set": {"status": "enviado", "sent_at": datetime.datetime.utcnow()}}
                )
            if comandos_para_enviar:
                app.logger.info(f"Enviando comandos {comandos_para_enviar} para {device_id}")
        except Exception as e:
            app.logger.error(f"Erro ao buscar comandos no MongoDB: {e}")
            return jsonify({"error": "Erro ao buscar comandos"}), 500

    return jsonify(comandos_para_enviar)  # Retorna a lista de strings de comando


# --- Endpoint para o Cliente Flask (Futuro) ---
@app.route('/api/dados_recentes', methods=['GET'])
def obter_dados_recentes():
    if not client:
        return jsonify({"error": "Conexão com o banco de dados indisponível"}), 500
    try:
        registros = list(colecao_leituras.find().sort("timestamp", DESCENDING).limit(20))
        for r in registros:
            r["_id"] = str(r["_id"])
            r["timestamp"] = r["timestamp"].isoformat()
            if "received_at" in r and r["received_at"]:  # Checa se existe e não é None
                r["received_at"] = r["received_at"].isoformat()
        return jsonify(registros), 200
    except Exception as e:
        app.logger.error(f"Erro ao buscar dados recentes: {e}")
        return jsonify({"error": str(e)}), 500


# Modifique a função existente no nuvem.py
@app.route('/api/enviar_comando_atuador', methods=['POST'])
def enviar_comando_atuador_cliente():
    global cache_ultimo_estado

    if not client:
        return jsonify({"error": "Conexão com o banco de dados indisponível"}), 500

    data = request.json
    device_id = data.get('device_id')
    comando = data.get('comando')

    if not device_id or not comando:
        return jsonify({"error": "device_id e comando são obrigatórios"}), 400

    try:
        # Salva o comando no banco
        colecao_comandos.insert_one({
            "device_id": device_id,
            "comando": comando,
            "status": "pendente",
            "created_at": datetime.datetime.utcnow()
        })

        # ✅ ATUALIZA O CACHE IMEDIATAMENTE baseado no comando enviado
        if cache_ultimo_estado and isinstance(comando, str):
            # Identifica qual atuador e qual ação
            atuador_mapeamento = {
                'Irrigador': 'estadoIrrigador',
                'Lampada': 'estadoLampada',
                'Aquecedor': 'estadoAquecedor',
                'Refrigerador': 'estadoRefrigerador'
            }

            for atuador, estado_key in atuador_mapeamento.items():
                if atuador in comando:
                    if "_ON" in comando or comando == f"toggle{atuador}_ON":
                        cache_ultimo_estado['estado_atuadores'][estado_key] = "ON"
                    elif "_OFF" in comando or comando == f"toggle{atuador}_OFF":
                        cache_ultimo_estado['estado_atuadores'][estado_key] = "OFF"

                    # ✅ ENVIA ATUALIZAÇÃO IMEDIATA VIA SSE
                    live_update_queue.put({
                        "device_id": device_id,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "luminosidade": cache_ultimo_estado.get('luminosidade', 0),
                        "umidade": cache_ultimo_estado.get('umidade', 0),
                        "temperatura": cache_ultimo_estado.get('temperatura', 0),
                        "estado_atuadores": cache_ultimo_estado['estado_atuadores'],
                        "fonte": "comando_manual"  # Indica que veio de comando manual
                    })
                    break

        app.logger.info(f"Comando '{comando}' para '{device_id}' enfileirado e cache atualizado.")
        return jsonify({"message": f"Comando '{comando}' para '{device_id}' enfileirado."}), 200

    except Exception as e:
        app.logger.error(f"Erro ao enfileirar comando no MongoDB: {e}")
        return jsonify({"error": "Erro ao salvar comando"}), 500


# --- Lógica de Relatório ---
def criar_relatorio_nuvem_completo():  # Renomeei para evitar confusão se a outra ainda estiver no seu editor
    if not client:
        return "<strong>Conexão com o banco de dados indisponível para gerar relatório.</strong>", "Relatório Indisponível"

    if colecao_leituras.count_documents({}) == 0:
        return "<strong>Nenhum dado encontrado na coleção para gerar relatório.</strong>", "Relatório Vazio"

    registros = list(colecao_leituras.find().sort("timestamp", DESCENDING).limit(10))

    if not registros:
        return "<strong>Nenhum dado encontrado na coleção para gerar relatório.</strong>", "Relatório Vazio"

    temperaturas = [r["temperatura"] for r in registros if "temperatura" in r and r["temperatura"] is not None]
    luminosidades = [r["luminosidade"] for r in registros if "luminosidade" in r and r["luminosidade"] is not None]

    # CORREÇÃO AQUI: Calcular todas as ativações
    ativacoes_irrigador = sum(r.get("irrigador_times_on", 0) for r in registros)
    ativacoes_lampada = sum(r.get("lampada_times_on", 0) for r in registros)  # Adicionado
    ativacoes_aquecedor = sum(r.get("aquecedor_times_on", 0) for r in registros)  # Adicionado
    ativacoes_refrigerador = sum(r.get("refrigerador_times_on", 0) for r in registros)  # Adicionado

    umidadebool = registros[0].get("umidade")  # Usar .get() para segurança
    umidadetexto = 'N/A'
    if umidadebool == 0:
        umidadetexto = 'Molhado'
    elif umidadebool == 1:
        umidadetexto = 'Seco'

    try:
        mais_recente_dt = registros[0]["timestamp"]
        mais_antigo_dt = registros[-1]["timestamp"]
        # Formatando para string. Se o fuso horário for importante, adicione .astimezone(seu_fuso_horario)
        # mas lembre-se que fromisoformat e utcnow são timezone-aware ou naive (UTC).
        mais_recente = mais_recente_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
        mais_antigo = mais_antigo_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception as e:
        app.logger.error(f"Erro ao formatar timestamp no relatório: {e}")
        mais_recente = "N/A"
        mais_antigo = "N/A"

    assunto_relatorio = f"Relatório Estufa Cloud | {mais_antigo} → {mais_recente}"

    report_html = f"""
        <h2>Relatório das Últimas Leituras (Nuvem)</h2>
        <p><strong>Total de registros analisados:</strong> {len(registros)}</p>
        <p><strong>Mais recente:</strong> {mais_recente}<br>
           <strong>Mais antigo:</strong> {mais_antigo}</p>
        <h3>Dados da Leitura Recente ({registros[0]["timestamp"].strftime("%H:%M:%S UTC") if registros else ''})</h3>
        <ul>
            <li><strong>Luminosidade:</strong> {registros[0].get("luminosidade", "N/A")}</li>
            <li><strong>Umidade:</strong> {umidadetexto}</li>
            <li><strong>Temperatura:</strong> {registros[0].get("temperatura", "N/A")} °C</li>
        </ul>"""
    if temperaturas:
        report_html += f"""
        <h3>🌡️ Temperatura (Últimos {len(temperaturas)} registros com temperatura)</h3>
        <ul>
            <li><strong>Maior:</strong> {max(temperaturas):.2f} °C</li>
            <li><strong>Menor:</strong> {min(temperaturas):.2f} °C</li>
            <li><strong>Média:</strong> {sum(temperaturas) / len(temperaturas):.2f} °C</li>
        </ul>"""
    if luminosidades:
        report_html += f"""
        <h3>💡 Luminosidade (Últimos {len(luminosidades)} registros com luminosidade)</h3>
        <p><strong>Média:</strong> {sum(luminosidades) / len(luminosidades):.2f}</p>"""

    report_html += f"""
        <h3>⚙ Atuadores acionados (soma dos últimos {len(registros)} snapshots)</h3>
        <ul>
            <li>Irrigador: {ativacoes_irrigador} vezes</li>
            <li>Lâmpada: {ativacoes_lampada} vezes</li>
            <li>Aquecedor: {ativacoes_aquecedor} vezes</li>
            <li>Refrigerador: {ativacoes_refrigerador} vezes</li>
        </ul>
        """
    return report_html.strip(), assunto_relatorio


@app.route('/api/gerar_e_enviar_relatorio', methods=['POST'])
def rota_enviar_relatorio():
    data = request.json
    email_destinatario = data.get('email') if data else None

    if not email_destinatario:  # Se não veio email no corpo, usa o default do .env
        email_destinatario = TO_EMAIL

    if not email_destinatario:  # Se ainda não tem email, retorna erro
        return jsonify({"error": "E-mail do destinatário não fornecido e não configurado como default."}), 400

    if not SENDGRID_API_KEY or not FROM_EMAIL:
        return jsonify({"error": "Configuração de SendGrid (API Key ou From Email) incompleta"}), 500

    html_content, assunto_email = criar_relatorio_nuvem_completo()

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=email_destinatario,  # USA O E-MAIL RECEBIDO OU DEFAULT
        subject=assunto_email,
        html_content=html_content)
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        app.logger.info(f"Relatório enviado para {email_destinatario} via SendGrid: {response.status_code}")
        return jsonify({"message": f"Relatório enviado com sucesso para {email_destinatario}."}), 200
    except Exception as e:
        app.logger.error(f"Erro ao enviar relatório para {email_destinatario} via SendGrid: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv("PORT", 8080))
    app.logger.info(f"Iniciando servidor Flask na porta {port}")
    app.run(host='0.0.0.0', port=port, debug=True)