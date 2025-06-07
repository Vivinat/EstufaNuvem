# Usa uma imagem leve do Python
FROM python:3.11-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia os arquivos de dependência e instala os pacotes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o conteúdo da aplicação para o container
COPY . .

# Define variáveis de ambiente padrão para o Flask
ENV FLASK_APP=nuvem.py
ENV FLASK_RUN_PORT=8080
ENV FLASK_ENV=production
ENV PORT=8080

# Expõe a porta que o OpenShift espera
EXPOSE 8080

# Comando para rodar a aplicação
CMD ["python", "nuvem.py"]
