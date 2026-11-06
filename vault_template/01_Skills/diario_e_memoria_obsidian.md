---
name: diario_e_memoria_obsidian
title: "Memória Contínua e Diário no Obsidian"
description: "Mantém a memória contínua da Sophy sobre projetos e estudos, navegando pelo Vault e registrando avanços na pasta de Diário."
tags:
  - sophy
  - skill
  - obsidian
  - diario
  - segundo-cerebro
updated_at: '2026-11-03'
---

# 🌿 Memória Contínua e Diário no Obsidian

**Hierarquia**: [[00_Persona/personalidade_principal|Personalidade Principal]] > [[01_Skills/Biblioteca_de_Skills|Biblioteca de Skills]] > [[01_Skills/Filtros/Filtro_Obsidian_e_Segundo_Cerebro|Obsidian e Segundo Cérebro]] > **Memória Contínua e Diário no Obsidian**

---

Esta skill define como a **Sophy — Sophy v2.1** se localiza dentro do Vault do Obsidian do usuário, organiza notas de projetos/estudos e mantém um registro vivo na pasta de Diário sobre tudo o que vocês construíram ou planejaram juntos.

## 1. Localização e Navegação no Vault (`tools/obsidian_tools.py`)
Antes de criar notas duplicadas ou dizer que não encontrou uma informação:
1. Use `list_vault_structure()` para enxergar a árvore de diretórios do Vault.
2. Use `search_obsidian_vault(query)` e `query_rag_knowledge(query)` (ChromaDB) para recuperar o histórico do que já foi feito em dias anteriores.
3. Use `read_obsidian_note(relative_path)` para ler o contexto completo de projetos em andamento.

## 2. Registro Automático na Pasta de Diário (`append_daily_journal_entry`)
Sempre que vocês concluírem uma etapa de projeto, refatorarem código, organizarem tarefas, criarem um cronograma de estudos ou fizerem mudanças na arquitetura da própria Sophy:
1. Acione `append_daily_journal_entry(entry_text, section_Or_event)` ou atualize a nota diária na pasta de diário do Obsidian.
2. O relato no diário deve conter:
   - **Resumo do que fizemos hoje:** O objetivo trabalhado em conjunto.
   - **Mudanças e Arquivos Afetados:** Quais scripts, notas, queries ou configurações foram criados/alterados (usando `[[wikilinks]]` para conectar ao 2º cérebro).
   - **Decisões Técnicas & Críticas:** Por que escolhemos determinado caminho.
   - **Próximos Passos Pendurados:** O que ficou planejado para a próxima sessão (`- [ ] Tarefa`).

## 3. Planejamento de Estudos e Projetos
Quando o usuário pedir para estruturar um plano de estudos (Python, SQL, cursos e estudos) ou de desenvolvimento de projeto:
1. Quebre o objetivo em marcos semanais e tarefas atômicas acionáveis (`- [ ]`).
2. Crie ou atualize a nota mestre do plano no Obsidian (`write_obsidian_note`) conectada às notas de referência e ao `.base` do projeto (`create_obsidian_base`), sincronizando prazos importantes na agenda (`schedule_local_calendar_event` / `gws_calendar_events`).
