# Sophy — assistente local para estudo, organização e programação

A Sophy é uma assistente de IA que roda **no seu computador**. Ela conversa com você no terminal ou num painel web, mexe nas suas notas do Obsidian, lê seus PDFs de estudo, revisa seu código Python de forma didática, cuida da sua agenda do Google e registra num diário o que fez por você.

## Para que serve (objetivo)

- **Estudar com você:** ler apostilas e PDFs, explicar conteúdos, procurar um assunto dentro de um PDF e revisar seu código com explicações (ela não escreve o código no seu lugar).
- **Organizar sua vida digital:** criar e editar notas, pastas e subpastas no seu cofre Obsidian; criar pastas em Documentos e Downloads; manter sua agenda do Google.
- **Ser honesta sobre o que fez:** cada ação passa por uma ferramenta real. Se ela não usou uma ferramenta, avisa que nada foi feito.
- **Privacidade primeiro:** o modelo principal roda localmente (Ollama). A nuvem é opcional e só entra se você preencher uma chave. Existe um modo offline que bloqueia qualquer chamada à internet.

Por que ela existe: montar um assistente que **faz** coisas no seu computador (e não só conversa) exige cuidado com permissões, confirmações e registro. Este projeto é um exemplo de como fazer isso com ferramentas locais e regras claras.

## Status das funções

Testado = eu executei a função de ponta a ponta no meu computador (Linux, Ollama, GPU NVIDIA de 6 GB). Configuração = precisa de conta ou chave sua. Não testado = o código existe, mas não validei nesta versão.

| Função | Status | O que é preciso |
| --- | --- | --- |
| Conversa no terminal com o modelo local | Testado | Ollama e o modelo `qwen2.5:7b-instruct-q4_K_M` |
| Conversa com Gemini (nuvem) | Testado | Chave `GOOGLE_AI_STUDIO_API_KEY` |
| Listar, ler, criar e editar notas no cofre | Testado | `OBSIDIAN_VAULT_PATH` apontando para uma pasta |
| Listar a árvore de pastas do cofre | Testado | o mesmo cofre |
| Criar pastas no cofre | Testado | o mesmo cofre |
| Criar pastas em Documents, Downloads ou nos projetos | Testado | estar dentro de `ALLOWED_CODE_DIRS` ou `READ_ONLY_DIRS` |
| Ler PDFs e buscar um assunto dentro deles | Testado | `pdftotext` (pacote poppler-utils). PDF escaneado não é lido (sem OCR) |
| Revisão didática de arquivo Python | Testado | regras básicas: `eval`, `except` sem tipo, padrão mutável, imports não usados, funções longas |
| Google Calendar: listar, criar, editar, apagar | Testado | `gws-cli` autenticado na sua conta Google |
| Editar o título de um evento por linguagem natural | Testado | o mesmo acesso ao Google |
| Editar horário ou local de um evento | Parcial | depende do modelo; ainda não é confiável |
| Apagar evento | Testado | pede confirmação antes de apagar |
| Diário automático (comandos, ajuda e alterações por dia) | Testado | nenhuma; a nota fica no cofre, em `03_Diario` |
| Painel web com telemetria (porta 8080) | Testado | `main.py` sobe o painel; `GUI_PORT` muda a porta |
| Modo offline (`--offline`) | Testado | nenhuma; bloqueia nuvem e Google |
| Guarda contra "ação inventada" | Testado | nenhuma; avisa quando a resposta diz que fez algo sem ferramenta |
| Discord (conversa no canal) | Configuração | token do bot, servidor e canal |
| WhatsApp | Não testado | pareamento por QR code (`sophy_auth_helper.sh whatsapp`) |
| Gmail e Google Drive | Não testado | mesmo `gws-cli` do calendário, com os escopos do Gmail/Drive |
| Memória semântica (RAG com ChromaDB) | Não testado | modelo `nomic-embed-text`, depois indexar o cofre |
| Flashcards com repetição espaçada (SM-2) | Não testado | nenhuma |
| Controle de ventoinhas e luz RGB | Específico | só funciona em notebooks compatíveis com o driver (TUXEDO/Uniwill). Em outras máquinas a ferramenta devolve erro e o resto continua funcionando |
| Spotify | Não testado | `SPOTIPY_*` e autorização na primeira execução |
| Finanças (cotações, cripto) | Não testado | chaves opcionais de API (`ALPHA_VANTAGE_API_KEY` etc.) |
| Catálogo de skills externas | Não testado | a pasta `external_skills` não vem no repositório; baixe os projetos citados nela se quiser |

## Requisitos

- Linux (testado no Linux; no Windows e no macOS não validei).
- Python 3.12 ou mais novo.
- [Ollama](https://ollama.com) instalado e rodando.
- Pelo menos 8 GB de RAM. Com placa NVIDIA de 6 GB ou mais, o modelo roda mais rápido na GPU; sem GPU funciona, mais devagar.
- `pdftotext` (pacote `poppler-utils` no Debian/Ubuntu ou `poppler` no Arch) para ler PDFs.
- Opcionais: `uv` (para o `gws-cli` do Google) e a chave de API do Gemini.

## Passo a passo de instalação

### 1. Baixe o projeto

```bash
git clone https://github.com/SEU_USUARIO/sophy.git
cd sophy
```

### 2. Crie o ambiente Python e instale as dependências

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 3. Instale o modelo local

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M
ollama pull nomic-embed-text
```

### 4. Crie o seu cofre do Obsidian

O cofre é uma pasta comum. Recomendo mantê-lo **fora** do repositório, para não misturar suas notas com o código:

```bash
mkdir -p ~/Documents/Sophy-Vault
cp -r vault_template/. ~/Documents/Sophy-Vault/
```

Você pode abrir essa pasta no Obsidian normalmente.

### 5. Configure o arquivo `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Edite o `.env` e preencha pelo menos estes campos:

```bash
CREATOR_NAME=seu_nome                       # como a Sophy vai te chamar
OBSIDIAN_VAULT_PATH=/home/SEU_USUARIO/Documents/Sophy-Vault
ALLOWED_CODE_DIRS=/home/SEU_USUARIO/Projects   # pastas de código que ela pode ler e editar
READ_ONLY_DIRS=/home/SEU_USUARIO/Documents,/home/SEU_USUARIO/Downloads
```

Use caminhos absolutos ou com `~`. Não coloque aspas.

### 6. Teste a instalação

```bash
.venv/bin/python main.py --check
```

O painel de status deve aparecer. Linhas com "não configurada" são normais: são as integrações que você ainda não ligou.

Teste uma conversa:

```bash
.venv/bin/python main.py --no-gui --prompt "Oi! Quem é você?"
```

### 7. Crie o comando `sophy` (opcional)

```bash
./scripts/instalar_atalho.sh
sophy
```

Se `sophy` não for encontrado, adicione `~/.local/bin` ao `PATH`.

## Como usar (comandos)

| Comando | Para que serve |
| --- | --- |
| `sophy` ou `python main.py` | Abre o painel de status, o painel web e o chat no terminal |
| `sophy --check` | Mostra o painel de status e sai |
| `sophy --no-gui --prompt "pergunta"` | Faz uma pergunta e sai (útil em scripts) |
| `sophy --no-gui` | Só o chat no terminal, sem abrir o painel |
| `sophy --gui` | Só o painel web (http://127.0.0.1:8080) |
| `sophy --offline` | Igual ao `sophy`, mas sem nenhuma chamada à internet |

Dentro do chat: `/status`, `/painel`, `/limpar` (zera a conversa) e `/sair`.

Exemplos de pedidos que funcionam bem:

- "Liste as pastas e subpastas do meu vault."
- "Crie a pasta Estudos/Python dentro do vault."
- "Leia o PDF ~/Documents/Estudos/apostila.pdf e me explique a página 3."
- "Procure 'chave primária' no PDF ~/Documents/Estudos/apostila.pdf."
- "Revise meu código em ~/Projects/teste/main.py e me diga o que melhorar, sem reescrever."
- "Quais são os meus eventos nos próximos dias?"
- "Marque amanhã às 14h uma reunião de estudo de 1 hora."
- "Mude o título do evento Estudo de SQL para Estudo de SQL - revisão."
- "Apague o evento Reunião de teste." (ela mostra o evento e espera seu "sim")
- "Crie uma pasta chamada Pos_Analise_Dados em Documents."
- "Quais ferramentas você usou hoje?" (o diário registra isso)

## Configurar as integrações (opcional)

### Gemini (nuvem, mais capacidade)

1. Crie uma chave em https://aistudio.google.com/apikey.
2. Coloque em `GOOGLE_AI_STUDIO_API_KEY` no `.env`.
3. Modelos: `GOOGLE_AI_MODEL` (ex.: `gemini-3.1-flash-lite`). Nomes de modelos mudam com o tempo; se der erro 404, consulte a lista atual do Google.

Quando há internet e a chave existe, a Sophy usa o Gemini; sem internet, usa o modelo local.

### Google Calendar, Gmail e Drive (via gws-cli)

1. No [Google Cloud Console](https://console.cloud.google.com), crie um projeto, ative a **Google Calendar API** (e Gmail/Drive, se quiser) e crie um cliente OAuth do tipo **App para computador**.
2. Baixe o JSON do cliente e salve em `~/.config/gws-cli/client_secret.json`.
3. Instale o gws-cli: `uv tool install gws-cli` (precisa do `uv`).
4. Autorize sua conta: `gws-cli auth` (abre o navegador).
5. Teste: `gws-cli calendar list`. Se listar seus eventos, a Sophy também consegue.

Com `STRICT_OFFLINE_MODE=true` (ou `--offline`), nada disso é acessado.

### Discord (conversa no canal)

1. Crie um aplicativo em https://discord.com/developers/applications, depois um **Bot**, e copie o token.
2. Ative as intents **Message Content** e **Server Members** no bot.
3. Convide o bot para o seu servidor com permissão de ler e enviar mensagens no canal.
4. Preencha `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID` (ID do servidor) e `DISCORD_CHANNEL_ID` (ID do canal). Para ver IDs, ative o modo desenvolvedor no Discord.

### WhatsApp

Veja a seção "Pareamento" do script `sophy_auth_helper.sh`:

```bash
./sophy_auth_helper.sh whatsapp
```

Ainda não validei o WhatsApp de ponta a ponta nesta versão.

## Personalizar a personalidade

- A persona fica no seu cofre, no arquivo `00_Persona/personalidade_principal.md`. Ela é lida a cada conversa, então você pode editar à vontade.
- `{CREATOR_NAME}` vira o nome que você colocou no `.env`. `{VAULT_PATH}` vira o caminho do cofre.
- A seção "Regra de Honestidade sobre Ações" não deve ser removida: ela impede a Sophy de dizer que fez algo que não fez.

## Como ela decide o que fazer (resumo técnico)

1. Cada pergunta passa por um **roteamento**: ela escolhe um conjunto pequeno de ferramentas conforme o assunto (cofre, PDF, agenda, pasta, código). Isso evita que um modelo de 7 bilhões de parâmetros se perca entre dezenas de ferramentas.
2. Perguntas simples e frequentes (listar cofre, listar agenda, revisar um arquivo Python, apagar ou renomear um evento) têm **atalhos determinísticos**: a resposta vem do disco ou da API, não do modelo.
3. Ações destrutivas pedem **confirmação**: apagar evento, por exemplo.
4. Um **guarda** acrescenta um aviso quando a resposta diz "criei", "apaguei" ou "executei" sem que nenhuma ferramenta tenha sido chamada.
5. Cada conversa grava no **diário** do cofre (`03_Diario/AAAA-MM-DD.md`): comandos usados, o que ela ajudou e o que foi alterado.

## Estrutura do projeto

```text
config.py            configurações (lê o .env)
main.py              ponto de entrada: terminal, painel e modo --prompt
core/
  agent_core.py      orquestrador: roteamento, ferramentas, atalhos e guardas
  persona_filter.py  carrega a persona do cofre
  diario.py          diário automático
tools/               ferramentas que a Sophy pode usar
  obsidian_tools.py  cofre: notas, pastas, skills
  tutor_tools.py     PDFs e revisão de código
  google_calendar_tools.py  agenda do Google
  file_tools.py      pastas locais
  sql_tools.py       banco local (estudos, histórico)
workers/             Discord, sincronização e WhatsApp
gui/                 painel web (FastAPI + HTML/JS)
scripts/             atalhos e inicialização
vault_template/      modelo do cofre Obsidian (copie para fora do repositório)
```

## Segurança e privacidade

- **Chaves:** ficam só no `.env`, que está no `.gitignore`. Nunca publique o `.env`. Se uma chave vazar, revogue-a no provedor e crie outra.
- **Arquivos:** a Sophy só edita dentro de `ALLOWED_CODE_DIRS` e só lê dentro de `READ_ONLY_DIRS` e `ALLOWED_CODE_DIRS`. Ela não apaga arquivos do disco; apagar evento de agenda pede confirmação.
- **Nuvem:** só é usada com a chave preenchida. `STRICT_OFFLINE_MODE=true` ou `--offline` corta toda chamada externa.
- **Banco e diário:** ficam na sua máquina. O diário fica dentro do cofre.

## Limitações conhecidas

- Modelos locais de 7B erram às vezes. Para decisões importantes, confira.
- PDFs escaneados não são lidos (falta OCR, como o `tesseract`).
- A revisão de código cobre só algumas regras; não substitui testes nem um revisor humano.
- Editar horário ou local de evento ainda depende do modelo.
- Integrações marcadas como "Não testado" podem precisar de ajustes.

## Solução de problemas

- **"Ollama não responde"**: rode `ollama serve` ou inicie o serviço do Ollama.
- **"modelo não encontrado"**: rode `ollama pull` com o nome do modelo no `.env`.
- **Google "não autorizado"**: refaça `gws-cli auth` e confira se o `client_secret.json` está em `~/.config/gws-cli/`.
- **"pdftotext não encontrado"**: instale `poppler-utils` (Debian/Ubuntu) ou `poppler` (Arch).
- **Google não responde**: confira se `STRICT_OFFLINE_MODE` está `false` no `.env`.
- **Porta 8080 ocupada**: mude `GUI_PORT` no `.env`.

## Contribuir

Issues e pull requests são bem-vindos. Antes de enviar uma mudança, rode `.venv/bin/python -m py_compile` nos arquivos alterados e teste o comando `--check`.

## Licença

Este projeto está licenciado sob a licença MIT
