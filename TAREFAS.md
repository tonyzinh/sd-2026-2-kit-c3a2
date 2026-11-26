# TAREFAS - Trabalho C3.A2 (Plataforma de IA como Servico)

Este e o projeto integrador. Reaproveite o que voce fez em C1.A2 e C2.A2.

## Nucleo obrigatorio

- [x] **1. Autenticacao JWT no gateway** - `gateway/app.py`
      Rota de login que emite o token e protecao das demais rotas (401 sem token). (Aula 16)

- [x] **2. Rate limit no gateway** - `gateway/app.py`
      Limitar requisicoes por cliente por minuto. (Aula 8)

- [x] **3. Dockerfile de cada servico**
      Um Dockerfile por servico. Use `Dockerfile.exemplo` como ponto de partida. (Aula 13)

- [x] **4. docker-compose completo**
      O sistema inteiro deve subir com UM comando. (Aula 14)

- [x] **5. Deploy em nuvem (free tier)**
      Publicar e informar a URL publica no README. (Aula 13)

- [x] **6. Resiliencia**
      Tempo-limite, retentativa com espera progressiva e circuit breaker. (Aula 11)

- [x] **7. Observabilidade**
      Rastreamento distribuido cobrindo gateway -> fila -> worker. (Aula 15)

- [x] **8. HTTPS** no servico publicado. (Aula 16)

- [x] **9. Secao de conformidade no README**
      Quais dados sao tratados, com qual finalidade e por quanto tempo. (LGPD, Aula 16)

- [x] **10. Relatorio de trade-offs**
      CAP, custo e latencia do seu sistema.

## Extensoes opcionais

- [ ] Autoescalonamento de workers conforme o tamanho da fila
- [ ] Componente serverless ou na borda
- [ ] CI/CD publicando automaticamente a cada commit
- [ ] Painel de metricas
- [ ] Manifests de Kubernetes

## Antes de entregar

- [ ] Clone em OUTRA maquina e rode o comando unico - funciona?
- [x] A URL publica esta no ar?
- [x] Toda configuracao esta em variavel de ambiente (nada fixo no codigo)?
- [x] Os logs estao livres de dados pessoais?
