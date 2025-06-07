// static/js/app.js

const DEVICE_ID = "minhaEstufa01";
const listaLeiturasUl = document.getElementById('lista-leituras');

// Mapeamento para facilitar a atualização da UI
const atuadorElements = {
    'Irrigador': { statusId: 'status-Irrigador', btnOnId: 'btn-Irrigador-ON', btnOffId: 'btn-Irrigador-OFF' },
    'Lampada': { statusId: 'status-Lampada', btnOnId: 'btn-Lampada-ON', btnOffId: 'btn-Lampada-OFF' },
    'Aquecedor': { statusId: 'status-Aquecedor', btnOnId: 'btn-Aquecedor-ON', btnOffId: 'btn-Aquecedor-OFF' },
    'Refrigerador': { statusId: 'status-Refrigerador', btnOnId: 'btn-Refrigerador-ON', btnOffId: 'btn-Refrigerador-OFF' },
    'PilotoAutomatico': { statusId: 'status-PilotoAutomatico', btnOnId: 'btn-PilotoAutomatico-ON', btnOffId: 'btn-PilotoAutomatico-OFF' }
};

//CARREGAMENTO

let sistemaEmCarregamento = false;
let relatorioEmCarregamento = false;

function setEstadoCarregamento(ativo) {
    sistemaEmCarregamento = ativo;

    for (const key in atuadorElements) {
        const { btnOnId, btnOffId } = atuadorElements[key];
        document.getElementById(btnOnId).disabled = ativo;
        document.getElementById(btnOffId).disabled = ativo;
    }

    const statusLoading = ativo ? 'Aguarde...' : '';
    for (const key in atuadorElements) {
        const { statusId } = atuadorElements[key];
        const statusSpan = document.getElementById(statusId);
        if (statusSpan) {
            statusSpan.textContent = statusLoading || statusSpan.textContent;
        }
    }
}


document.addEventListener('DOMContentLoaded', function() {
    // Busca estado atual ao carregar a página
    buscarUltimoEstadoAtuador();
    buscarUltimaLeitura();
    carregarLimites();
});

function buscarUltimoEstadoAtuador(){
    fetch('/api/estado_atual')
      .then(response => response.json())
      .then(data => {
        if (data.estado_atuadores) {
            atualizar_interface_com_estado(data.estado_atuadores);
        }
      })
      .catch(err => console.error('Erro ao obter estado atual:', err));

    // Conecta ao stream SSE
    const eventSource = new EventSource('/stream');
    eventSource.addEventListener('live_leitura', function(event) {
        const data = JSON.parse(event.data);
        atualizar_interface_com_estado(data.estado_atuadores);
    });
}

function buscarUltimaLeitura() {
    fetch('/api/dados_recentes')
        .then(response => response.json())
        .then(data => {
            if (Array.isArray(data) && data.length > 0) {
                const leitura = data[0]; // Pega a mais recente
                mostrarLeituraSignificativa(leitura);
            } else {
                console.log('Nenhuma leitura recente disponível.');
            }
        })
        .catch(error => {
            console.error('Erro ao buscar dados recentes:', error);
        });
}


function mostrarLeituraSignificativa(leitura) {
    const lista = document.getElementById('lista-leituras');
    lista.innerHTML = '';  // Limpa a lista

    const dt = new Date(leitura.timestamp);
    const dataFormatada = dt.toLocaleString('pt-BR', { hour12: false });

    const umidadeTexto = leitura.umidade == 0 ? 'Molhado (0)' : 'Seco (1)';

    const estado = leitura.estado_atuadores || {};

    const item = document.createElement('li');
    item.textContent = `OBTIDO DO BANCO EM [${dataFormatada}] Temp: ${leitura.temperatura}°C, Umi: ${umidadeTexto}, Lum: ${leitura.luminosidade} | Atuadores: ` +
        `Irrigador: ${estado.estadoIrrigador || 'N/A'}, ` +
        `Lampada: ${estado.estadoLampada || 'N/A'}, ` +
        `Aquecedor: ${estado.estadoAquecedor || 'N/A'}, ` +
        `Refrigerador: ${estado.estadoRefrigerador || 'N/A'}, ` +
        `PilotoAutomatico: ${estado.estadoPilotoAutomatico || 'N/A'}`;

    lista.appendChild(item);
}

function atualizarLimites() {
    const limiteTemp = parseFloat(document.getElementById('inputLimiteTemp').value);
    const limiteLuz = parseFloat(document.getElementById('inputLimiteLuz').value);

    if (isNaN(limiteTemp) || limiteTemp < 10 || limiteTemp > 50) {
        alert("Limite de Temperatura deve estar entre 10°C e 50°C.");
        return;
    }
    if (isNaN(limiteLuz) || limiteLuz < 100 || limiteLuz > 1000) {
        alert("Limite de Luminosidade deve estar entre 100 e 1000 Lux.");
        return;
    }

    fetch('/api/atualizar_limites', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            device_id: 'minhaEstufa01',
            limiteTemp: limiteTemp,
            limiteLuz: limiteLuz
        })
    }).then(res => res.json()).then(data => {
        alert(data.message || 'Limites atualizados com sucesso!');
    }).catch(err => {
        console.error('Erro:', err);
        alert('Erro ao atualizar limites.');
    });
}

function carregarLimites() {
    fetch('/api/limites_atuais')
    .then(res => res.json())
    .then(data => {
        document.getElementById('inputLimiteTemp').value = data.limiteTemp;
        document.getElementById('inputLimiteLuz').value = data.limiteLuz;
    }).catch(err => {
        console.error('Erro ao carregar limites:', err);
    });
}



function atualizarStatusAtuadorUI(nomeAtuador, estado) {
    const elements = atuadorElements[nomeAtuador];
    if (!elements) return;

    const statusSpan = document.getElementById(elements.statusId);
    const btnOn = document.getElementById(elements.btnOnId);
    const btnOff = document.getElementById(elements.btnOffId);

    statusSpan.textContent = estado;

    // Sempre limpa classes antes
    btnOn.classList.remove('ativo-on', 'ativo-off', 'ligado', 'desligado');
    btnOff.classList.remove('ativo-on', 'ativo-off', 'ligado', 'desligado');

    if (estado === 'ON') {
        btnOn.classList.add('ativo-on'); // verde
        btnOn.disabled = true;

        btnOff.disabled = false;
    } else {
        btnOff.classList.add('ativo-off'); // cinza
        btnOff.disabled = true;

        btnOn.disabled = false;
    }
}

function atualizarEstadoPilotoSimulado(ligado) {
    const estado = ligado ? 'ON' : 'OFF';
    atualizarStatusAtuadorUI('PilotoAutomatico', estado);
    aplicarBloqueioPorPiloto(estado);
}

function atualizar_interface_com_estado(estadoAtuadores) {
    for (const key in atuadorElements) {
        const estadoKey = `estado${key}`;
        if (estadoAtuadores.hasOwnProperty(estadoKey)) {
            atualizarStatusAtuadorUI(key, estadoAtuadores[estadoKey]);
        }
    }
    aplicarBloqueioPorPiloto(estadoAtuadores.estadoPilotoAutomatico);
}




// --- Conectar ao Stream SSE para Leituras Ao Vivo ---
if (!!window.EventSource) {
    const source = new EventSource('/stream');

    source.addEventListener('live_leitura', function(event) {
        console.log("Dados SSE (live_leitura):", event.data);
        const leitura = JSON.parse(event.data);

        if (leitura.device_id === DEVICE_ID) {
            const timestamp = new Date(leitura.timestamp).toLocaleString('pt-BR');
            const item = document.createElement('li');

            let estadoAtuadoresLidos = leitura.estado_atuadores || {};
            let estadoAtuadoresStr = Object.entries(estadoAtuadoresLidos)
                                         .map(([key, value]) => `${key.replace('estado', '')}: ${value}`)
                                         .join(', ');

            item.textContent = `[${timestamp}] Temp: ${leitura.temperatura}°C, Umi: ${leitura.umidade === 0 ? 'Molhado' : 'Seco'} (${leitura.umidade}), Lum: ${leitura.luminosidade} | Atuadores: ${estadoAtuadoresStr || 'N/A'}`;

            if (listaLeiturasUl.firstChild && listaLeiturasUl.firstChild.textContent.includes('Aguardando')) {
                listaLeiturasUl.innerHTML = '';
            }
            listaLeiturasUl.insertBefore(item, listaLeiturasUl.firstChild);
            while (listaLeiturasUl.children.length > 15) {
                listaLeiturasUl.removeChild(listaLeiturasUl.lastChild);
            }

            // Atualizar UI dos botões e status
            atualizar_interface_com_estado(estadoAtuadoresLidos);
            setEstadoCarregamento(false);

        }
    });

    source.onopen = function() {
        console.log("Conexão SSE aberta.");
        if (listaLeiturasUl.firstChild && listaLeiturasUl.firstChild.textContent.includes('Aguardando')) {
             listaLeiturasUl.innerHTML = '<li>Aguardando dados ao vivo...</li>';
        }
    };
    source.onerror = function(err) {
        console.error("Erro na conexão SSE:", err);
        if (listaLeiturasUl.firstChild && listaLeiturasUl.firstChild.textContent.includes('Aguardando')) {
            listaLeiturasUl.innerHTML = '<li>Erro na conexão para dados ao vivo. Verifique o console.</li>';
        }
    };
} else {
    console.warn("Seu navegador não suporta Server-Sent Events.");
    listaLeiturasUl.innerHTML = '<li>Seu navegador não suporta atualizações ao vivo.</li>';
}

// --- Enviar Comandos (funções enviarComando, enviarComandoPiloto, solicitarRelatorio permanecem iguais) ---
async function enviarComando(comando) {
    const nomeAtuador = comando.replace('toggle', '').split('_')[0]; // Ex: 'Irrigador'
    console.log(`Enviando comando: ${comando} para device: ${DEVICE_ID}`);

    // ✅ Feedback imediato visual
    const elements = atuadorElements[nomeAtuador];
    if (elements) {
        const statusSpan = document.getElementById(elements.statusId);
        statusSpan.textContent = 'Enviando...';
        statusSpan.style.color = '#orange';
    }

    try {
        const response = await fetch('/api/enviar_comando_atuador', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: DEVICE_ID, comando: comando }),
        });
        const resultado = await response.json();

        if (response.ok) {
            console.log(`Comando '${comando}' enviado! Mensagem: ${resultado.message}`);

            // ✅ Simula o estado esperado imediatamente
            if (elements) {
                const statusSpan = document.getElementById(elements.statusId);
                // Determina o estado esperado baseado no comando
                const estadoEsperado = comando.includes('_ON') ? 'ON' : 'OFF';

                statusSpan.textContent = `${estadoEsperado} (aguardando confirmação...)`;
                statusSpan.style.color = '#blue';

                // Simula visualmente o estado
                atualizarStatusAtuadorUI(nomeAtuador, estadoEsperado);

                // Remove a indicação visual após receber confirmação via SSE
                setTimeout(() => {
                    if (statusSpan.textContent.includes('aguardando confirmação')) {
                        statusSpan.style.color = ''; // Remove cor especial
                    }
                }, 10000); // Remove após 10s se não houver confirmação
            }

        } else {
            console.log(`Erro ao enviar comando '${comando}': ${resultado.error || response.status}`);
            // ✅ Restaura estado em caso de erro
            if (elements) {
                const statusSpan = document.getElementById(elements.statusId);
                statusSpan.textContent = 'Erro no comando';
                statusSpan.style.color = '#red';
            }
        }
    } catch (error) {
        console.error("Falha ao enviar comando:", error);
        // ✅ Restaura estado em caso de erro
        if (elements) {
            const statusSpan = document.getElementById(elements.statusId);
            statusSpan.textContent = 'Falha na conexão';
            statusSpan.style.color = '#red';
        }
    }
}

async function enviarComandoPiloto(ligar) {
    const comandoPayload = {
        device_id: DEVICE_ID,
        comando: { command: "set_auto_mode", value: ligar }
    };
    console.log(`Enviando comando piloto: `, comandoPayload.comando);

    setEstadoCarregamento(true);

    try {
        const response = await fetch('/api/enviar_comando_atuador', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(comandoPayload),
        });
        const resultado = await response.json();

        if (response.ok) {
            console.log(`Comando de piloto automático enviado! ${resultado.message}`);
        } else {
            console.log(`Erro ao enviar comando de piloto: ${resultado.error || response.status}`);
        }
    } catch (error) {
        console.error("Falha ao enviar comando de piloto:", error);
        console.log(`Falha ao enviar comando de piloto. Verifique o console.`);
    } finally {
        setEstadoCarregamento(false);
        atualizarEstadoPilotoSimulado(ligar);  // ✅ simula imediatamente
    }
}



function aplicarBloqueioPorPiloto(estadoPiloto) {
    const bloqueado = (estadoPiloto === 'ON');

    for (const key in atuadorElements) {
        const { btnOnId, btnOffId } = atuadorElements[key];

        if (key === 'PilotoAutomatico') {
            // ✅ Se for Piloto, apenas o botão de desligar (OFF) deve ficar ativo quando ligado.
            document.getElementById(btnOnId).disabled = bloqueado;  // Se bloqueado, não pode ligar de novo
            document.getElementById(btnOffId).disabled = !bloqueado;  // Só pode desligar quando está ligado
        } else {
            // ✅ Bloqueia todos os outros controles se Piloto está ON
            document.getElementById(btnOnId).disabled = bloqueado;
            document.getElementById(btnOffId).disabled = bloqueado;
        }
    }
}

async function solicitarRelatorio() {
    if (relatorioEmCarregamento) {
        alert("Relatório já está sendo enviado. Aguarde.");
        return;
    }

    relatorioEmCarregamento = true;

    const emailInput = document.getElementById('email-relatorio');
    const email = emailInput.value.trim();
    const btnRelatorio = document.querySelector('#relatorio button');

    // ✅ Desabilita botão e coloca carregando
    btnRelatorio.disabled = true;
    const textoOriginal = btnRelatorio.textContent;
    btnRelatorio.textContent = 'Aguarde...';

    console.log(`Solicitando relatório para o e-mail: ${email || 'default do .env da nuvem'}`);
    try {
        const response = await fetch('/api/gerar_e_enviar_relatorio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email }),
        });
        const resultado = await response.json();
        if (response.ok) {
            console.log(`Solicitação de relatório enviada! Mensagem: ${resultado.message}`);
            alert("Relatório enviado com sucesso!");
        } else {
            console.log(`Erro ao solicitar relatório: ${resultado.error || response.status}`);
            alert("Erro ao enviar relatório: " + (resultado.error || response.status));
        }
    } catch (error) {
        console.error("Falha ao solicitar relatório:", error);
        alert("Falha ao solicitar relatório. Verifique o console.");
    } finally {
        // ✅ Libera botão e restaura texto
        btnRelatorio.disabled = false;
        btnRelatorio.textContent = textoOriginal;
        relatorioEmCarregamento = false;
    }
}
