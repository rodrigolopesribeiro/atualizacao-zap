# Atualizacao ZAP

Script de automação Selenium que atualiza diariamente os imóveis do CRM da Rio Orla no portal ZAP Imóveis (VivaReal), executando três etapas em sequência.

## O que o script faz

### Parte 1 — 10:00 h (automático)
- Aguarda até as 10h para abrir o Chrome
- Faz login no CRM (`rioorla.com.br/crm`)
- Filtra imóveis do captador "Rodrigo Lopes" divulgados no VivaReal
- Para cada imóvel: atualiza a data na descrição, troca a 7ª pela 8ª foto, **desmarca** o VivaReal e salva
- Dispara a sincronização com o ZAP em Integrações → Parceiros

### Parte Intermediária — verificação no Canal Pro
- Faz login em `canalpro.grupozap.com`
- Verifica a cada 10 minutos se os imóveis sumiram dos anúncios ativos
- Fica em loop até confirmar a remoção de todos (timeout: 8 horas)

### Parte 2 — após confirmação
- Reabre cada imóvel pelo código, **remarca** o VivaReal com a categoria original e salva
- Dispara novamente a sincronização com o ZAP

## Pré-requisitos

- Python 3.10+
- Google Chrome instalado
- Dependências Python (ver `requirements.txt`)

## Instalação

```bash
pip install -r requirements.txt
```

## Configuração

As credenciais estão no topo de `atualizacao_zap.py`:

```python
# CRM Rio Orla
USUARIO = "Rodrigo"
SENHA   = "..."

# Canal Pro (ZAP/OLX)
CANALPRO_EMAIL = "mkmarcoslopes@gmail.com"
CANALPRO_SENHA = "..."
```

Ajuste também os intervalos se necessário:

| Variável | Padrão | Descrição |
|---|---|---|
| `POLLING_INTERVAL_SECONDS` | 600 (10 min) | Intervalo entre verificações no Canal Pro |
| `MAX_WAIT_SECONDS` | 28800 (8 h) | Timeout máximo da Parte Intermediária |

## Como rodar

```bash
python atualizacao_zap.py
```

O script aguardará automaticamente até as 10:00 h antes de iniciar. Se já passar das 10h, aguardará até as 10h do dia seguinte.

## Estrutura do projeto

```
Atualizacao_ZAP/
├── atualizacao_zap.py   # script principal
├── requirements.txt      # dependências Python
└── CLAUDE.md             # documentação do projeto
```

## Agendamento em nuvem (GitHub Actions)

Para rodar sem o computador ligado, configure o workflow em `.github/workflows/` com cron `0 13 * * 1-5` (13:00 UTC = 10:00 BRT, segunda a sexta) e armazene as credenciais como GitHub Secrets.
