# Deploy no Render (free tier)

O [render.yaml](../render.yaml) cria tudo de uma vez: 4 web services e 1 Key Value.

1. Envie o código para o seu repositório no GitHub (branch `main`).
2. Crie uma conta em <https://render.com> e conecte o GitHub. Se a conta já tiver um
   Key Value gratuito, remova-o antes: só é permitido um por workspace.
3. No painel: **New > Blueprint** e selecione o repositório.
4. Preencha as variáveis que o painel pedir:
   - `USUARIOS`: formato `usuario:senha`, com senha de **12 ou mais caracteres**.
     Use uma senha nova, criada só para este trabalho, sem `,` nem `:`.
   - `LLM_API_KEY`: deixe vazia para o modo simulado.
   - `OTEL_EXPORTER_OTLP_ENDPOINT` e `OTEL_EXPORTER_OTLP_HEADERS`: opcionais. Sem
     eles, os spans aparecem nos logs de cada serviço. Se o painel não aceitar campo
     vazio, apague essas variáveis do `render.yaml`.
   - `TOKEN_INTERNO` e `JWT_SEGREDO` são gerados automaticamente.
5. **Apply** e aguarde os quatro serviços ficarem *Live*.
6. Abra o serviço `plataforma-ia-gateway`, copie a URL `https://...onrender.com` e
   teste de ponta a ponta:

   ```bash
   # bash
   SMOKE_USUARIO=seu_usuario SMOKE_SENHA='sua_senha' python scripts/smoke.py https://SUA-URL.onrender.com
   ```

   ```powershell
   # PowerShell
   $env:SMOKE_USUARIO="seu_usuario"; $env:SMOKE_SENHA="sua_senha"
   python scripts/smoke.py https://SUA-URL.onrender.com
   ```

7. Coloque a URL no topo do [README](../README.md) e informe ao professor o usuário e
   a senha de avaliação por um canal privado (por exemplo, no AVA). **Não** coloque a
   senha no repositório, que é público.

## Se algo falhar

| Sintoma | Causa provável |
|---|---|
| Serviço não sobe e o log mostra `RuntimeError: ... fraco` ou `USUARIOS` | Variável ausente ou senha curta |
| `503`/`504` logo após um período ocioso | Serviços acordando (cerca de 1 min cada); repita |
| Lote parado em `pendente` | Worker hibernado; consultar o status do lote o acorda. Veja o log dele |
| Dados sumiram | O Key Value gratuito não persiste entre reinícios (esperado) |
