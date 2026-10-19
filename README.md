# Editor de PDF

Aplicação simples para abrir, editar e salvar PDFs.

## Instalação

### 1. Pré-requisitos

- **Python 3.10 ou mais recente** — baixe em <https://www.python.org/downloads/>.
  No Windows, marque a opção **"Add python.exe to PATH"** durante a instalação.
- A interface usa o **Tkinter**, que já vem com o Python no Windows e no macOS.
  No Linux pode ser preciso instalá-lo à parte:
  - Debian/Ubuntu: `sudo apt install python3-tk`
  - Fedora: `sudo dnf install python3-tkinter`

Para conferir, abra um terminal e rode:

```
python --version
```

(no Linux/macOS o comando pode ser `python3`).

### 2. Baixar o código

Com Git:

```
git clone https://github.com/<seu-usuario>/<seu-repositorio>.git
cd <seu-repositorio>
```

Ou, sem Git: na página do repositório clique em **Code → Download ZIP**, extraia o arquivo e
abra um terminal dentro da pasta extraída.

### 3. Criar um ambiente virtual (recomendado)

Isola as bibliotecas do projeto do resto do sistema.

Windows (PowerShell):

```
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> Se o PowerShell bloquear o script, rode uma vez
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e tente novamente.
> No Prompt de Comando (cmd) use `.venv\Scripts\activate.bat`.

Linux/macOS:

```
python3 -m venv .venv
source .venv/bin/activate
```

O nome `(.venv)` aparece no início da linha do terminal quando o ambiente está ativo.

### 4. Instalar as dependências

```
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5. Executar

```
python app.py               # abre a janela vazia
python app.py arquivo.pdf   # já abre um arquivo
```

Nas próximas vezes basta entrar na pasta, ativar o ambiente (passo 3, só a linha de ativação) e
rodar o passo 5.

## Uso

- **Editar texto** (modo padrão): passe o mouse sobre um texto (ele fica destacado e a barra
  inferior mostra a fonte e o tamanho), clique, altere e pressione *Enter*. Deixe vazio para apagar.
- **Remover imagem**: troque o modo na barra superior e clique sobre a imagem.
- **Desfazer**: Ctrl+Z (até 30 passos). **Salvar**: Ctrl+S. **Salvar como**: Ctrl+Shift+S.
- Navegação: PageUp/PageDown, roda do mouse; Ctrl+roda ou Ctrl+/Ctrl- para zoom.

## Como a fonte é preservada

O texto novo é escrito na mesma posição, tamanho e cor do original. Para a fonte, nesta ordem:

1. a fonte embutida no próprio PDF, se ela contém todos os caracteres digitados;
2. a fonte de mesmo nome instalada no computador (ex.: Arial, Calibri, Times New Roman);
3. uma fonte padrão parecida (Helvetica/Times/Courier, com negrito/itálico) — a barra de status avisa
   quando isso acontece.

PDFs costumam embutir apenas as letras usadas no documento (subconjunto), por isso o passo 2 é
necessário quando você digita letras novas.

## Limitações

- A edição é feita por trecho (palavras/linha com a mesma fonte). Um texto muito mais longo que o
  original não quebra linha automaticamente e pode sobrepor o que vem ao lado.
- PDFs escaneados (texto como imagem) não têm texto editável.

## Licença

Este projeto usa o [PyMuPDF](https://github.com/pymupdf/PyMuPDF), distribuído sob a licença
**AGPL-3.0** (ou licença comercial da Artifex).
