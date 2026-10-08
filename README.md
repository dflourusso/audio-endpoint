# Audio Endpoint

Cada Orange Pi vira um endpoint de áudio do Music Assistant. O aparelho recebe o áudio pelo Sendspin e toca numa caixa Bluetooth. O Music Assistant fica em outro servidor. O endereço vem do DHCP; o acesso é pelo nome `.local`.

Este repositório não copia o [Sendspin Bluetooth Bridge](https://github.com/trudenboy/sendspin-bt-bridge). O Compose usa a imagem `ghcr.io/trudenboy/sendspin-bt-bridge:2.75.0`. A interface, a API, o instalador e o agente do host são nossos.

## 1. Arquitetura

O navegador fala só com a API local. A API não executa shell nem `bluetoothctl`. Bluetooth, pareamento e o protocolo Sendspin ficam no bridge, em `127.0.0.1:8080`. Hostname, reboot e atualização passam por um agente no host, com uma lista fechada de ações.

```
Navegador  ->  API (porta 80)  ->  agente Unix
                      |
                      +->  Sendspin Bluetooth Bridge  ->  BlueZ / PipeWire  ->  caixa
                      |
                      +->  Shairport Sync (AirPlay)   ->  PipeWire           ->  caixa
                                              |
                                              +->  Music Assistant (mDNS / Sendspin)
                                              +->  iPhone (AirPlay)
```

Cada caixa pareada vira um player do Music Assistant. USB, P2 e HDMI aparecem na página de áudio quando o hardware existe, mas esta versão não troca a reprodução para eles.

O iPhone manda qualquer aplicativo pelo AirPlay. O áudio entra no Orange Pi e sai na caixa que continua pareada nele. Android não fala AirPlay; nesses aplicativos o celular conecta direto na caixa. Não instalamos um receptor Spotify Connect na placa.

## 2. Requisitos

- Orange Pi com Ubuntu Server 24.04 ou Armbian, arquitetura `aarch64`
- microSD com espaço livre; o instalador limita o journal para não encher o cartão
- Rede Wi-Fi ou Ethernet por DHCP, na mesma LAN do Music Assistant
- Adaptador Bluetooth do próprio Orange Pi, ou um USB
- Caixa Bluetooth com perfil A2DP
- Music Assistant 2.3 ou mais novo, com o provider Sendspin ligado
- Conta capaz de usar `sudo`

No Mac de desenvolvimento dá para rodar os testes da API. O som e o pareamento só existem na placa.

## 3. Instalação

Na placa, como root:

```bash
sudo git clone https://github.com/dflourusso/audio-endpoint.git /opt/audio-endpoint
cd /opt/audio-endpoint
sudo ./install.sh
```

O instalador confere a arquitetura, instala Docker, BlueZ, Avahi e PipeWire se faltarem, cria o usuário `audioendpoint` para o áudio, sobe o agente e executa `docker compose up -d --build`. No fim mostra `http://<hostname>.local`.

Rode o instalador de novo sem medo: ele não apaga `.env`, `config/`, `bridge/` nem `data/`.

## 4. Configuração inicial

1. Abra `http://<hostname>.local` no celular, na mesma rede.
2. Em Bluetooth, busque a caixa, pareie e conecte. A caixa entra na frota do bridge e o Music Assistant passa a enxergá-la.
3. No Music Assistant, confirme o player Sendspin.
4. No iPhone, abra o AirPlay e escolha o hostname da placa, por exemplo `audio-sala`. A biblioteca do Music Assistant continua no player Sendspin.

A interface do bridge também escuta na porta 8080, porque a imagem usa a rede do host e não oferece bind só em localhost. Use a nossa interface. A porta 8080 fica na LAN enquanto a autenticação do bridge não for ligada.

## 5. Configuração do hostname

Em Sistema, informe um nome como `audio-sala`. A API valida letras minúsculas, números e hífen. O agente executa `hostnamectl set-hostname`, ajusta `/etc/hosts` e reinicia o Avahi. O `BRIDGE_NAME` acompanha o hostname, para o player aparecer como `Caixa @ audio-sala`.

O DHCP não muda. Quando o roteador entregar outro IP, `http://audio-sala.local` continua valendo.

## Configuração do Wi-Fi

A primeira rede é gravada com teclado e monitor, pelo NetworkManager da imagem. Depois disso, se a placa for levada para outro lugar, ela mesma abre um ponto de acesso.

No boot, e também depois de um pico de energia:

1. Por cerca de 3 minutos ela tenta as redes já salvas. O UDM Pro e os access points da Ubiquiti costumam voltar depois da placa, então ela espera.
2. Se continuar sem rota, sobe o ponto de acesso aberto `audio-setup`, em `http://192.168.4.1`, por 5 minutos.
3. Se um celular entrar nessa rede, o ponto de acesso fica ligado até a pessoa sair ou gravar uma rede nova.
4. Se ninguém entrar, o ponto de acesso desliga e o ciclo volta ao passo 1.

A página Wi-Fi lista as redes vistas antes de abrir o ponto de acesso, pede a senha e grava a conexão. A senha vai direto para o `nmcli`, não para um comando de shell, e fica só no NetworkManager da placa. Se a senha funcionar, o ponto de acesso desliga e a placa volta para `http://<hostname>.local`. Se falhar, o ponto de acesso volta e a página mostra o erro.

Enquanto `audio-setup` estiver no ar, a API só responde o status e a configuração do Wi-Fi. Reiniciar, atualizar, Bluetooth e hostname ficam recusados, porque a rede é aberta.

O rádio do Zero 2W não fica cliente e ponto de acesso ao mesmo tempo. Se a placa de expansão tiver Ethernet com rota, o ponto de acesso não sobe.

Se duas placas subirem `audio-setup` juntas, configure uma de cada vez. O celular não distingue qual das duas está em `192.168.4.1`.

## 6. Bluetooth

A página Bluetooth busca dispositivos, pareia, conecta, desconecta e esquece. Parear grava o vínculo no BlueZ do host (`/var/lib/bluetooth`) e registra o MAC em `BLUETOOTH_DEVICES` no config do bridge. Sem esse registro o Music Assistant não ganha um player. Conectar um aparelho já pareado pede ao bridge para reconectar. Esquecer remove o vínculo e a entrada da frota.

O bridge observa a caixa e tenta reconectar quando ela liga de novo. A política fica no config dele (`BT_CHECK_INTERVAL`, `BT_MAX_RECONNECT_FAILS`).

## 7. Sendspin

O bridge anuncia os players por mDNS (`_sendspin._tcp`) e escuta a partir da porta 8928. `SENDSPIN_SERVER=auto` faz o bridge achar o Music Assistant na LAN. Os dois lados precisam de rede no host (`network_mode: host`), senão o multicast não atravessa.

Não há um segundo cliente Sendspin para a saída de som da placa. Isso seria outro player.

## 8. AirPlay

Cada caixa Bluetooth da frota aparece no iPhone como um AirPlay, com o nome do player e o hostname, no mesmo formato do Music Assistant. Sem caixa na frota, não há anúncio. O iPhone escolhe qual caixa toca. O áudio de qualquer aplicativo, inclusive YouTube Music, Amazon Music e Spotify, chega nessa caixa. Ela continua pareada no Orange Pi.

Cada anúncio grava o áudio numa saída PipeWire só dele, que existe mesmo com a caixa desligada. Quando o bridge reconecta essa caixa, o áudio passa para ela. As outras caixas pareadas não recebem esse som.

Na página Bluetooth, cada caixa tem um webhook opcional. Vazio não chama ninguém. O aviso sai só quando o AirPlay daquela caixa começa, para o fim da música não ligar a caixa outra vez. O valor fica em `config/`, fora do Git e fora do config do bridge. Cada placa guarda o webhook da caixa que precisa ligar.

Se o AirPlay de uma caixa começa enquanto o Music Assistant toca nela, o webhook dessa caixa dispara e o Sendspin dela fica mudo até a pausa. Se o Music Assistant começa nessa caixa durante o AirPlay dela, só essa sessão AirPlay é cortada. As outras caixas seguem.

A automação que já liga a caixa quando o Music Assistant toca ganha esse webhook como outro gatilho, e também pausa o player do Music Assistant dessa sala. Pausar não é um play novo.

O Music Assistant também pode descobrir esses AirPlays. A reprodução da biblioteca continua no player Sendspin, que é o que sincroniza com o resto da casa.

## 9. Music Assistant

O servidor do Music Assistant descobre o player Sendspin sozinho. Não é preciso IP fixo no Orange Pi. A página Música mostra se o bridge está no ar, o nome do player, se o servidor está conectado, e se o AirPlay está anunciado.

## 10. mDNS

O Avahi do host anuncia o hostname. É isso que resolve `audio-sala.local` para a interface web. O anúncio do player Sendspin é outro serviço, feito pelo bridge. Os dois convivem no mesmo `avahi-daemon`. Não suba um segundo Avahi dentro de um container.

## 11. Atualização

Na placa, dentro de `/opt/audio-endpoint`:

```bash
git pull --ff-only
docker compose pull sendspin-bridge shairport-sync
docker compose up -d --build
```

O botão Atualizar faz a mesma sequência pelo agente. A imagem `audio-endpoint` é compilada na placa; as imagens do bridge e do Shairport Sync são baixadas. O agente recusa uma árvore Git suja ou um pull que não seja fast-forward, copia `.env`, `config/` e `bridge/` para `data/backups/<data>/` e só então sobe os containers. Não há reboot automático. A interface pode cair por alguns segundos enquanto a imagem da API é recriada. O progresso fica em `data/update-status.json`.

A tag do bridge está no `.env` (`BRIDGE_IMAGE`). A do AirPlay está em `SHAIRPORT_IMAGE`. Atualizar uma delas é mudar essa tag no Git e publicar. `latest` não é usado, para uma placa não mudar de versão sozinha.

Se a atualização subir e a interface não voltar:

```bash
cd /opt/audio-endpoint
git checkout <commit-anterior>
docker compose up -d --build
```

## 12. Backup

Guarde, fora do cartão:

- `/opt/audio-endpoint/.env`
- `/opt/audio-endpoint/config`
- `/opt/audio-endpoint/bridge`
- `/var/lib/bluetooth`

O último diretório tem as chaves de pareamento do BlueZ. Sem ele, a caixa pede pareamento de novo mesmo com o `config.json` restaurado.

## 13. Troubleshooting

Os casos de mDNS, Bluetooth, Sendspin, Music Assistant, áudio, Docker e o ponto de acesso `audio-setup` estão em [docs/troubleshooting.md](docs/troubleshooting.md).

## 14. Várias unidades

O mesmo repositório vai em cada placa. A diferença é o estado local, criado na primeira instalação e editado pela interface:

| Unidade | Hostname |
| --- | --- |
| Sala | `audio-sala` |
| Quarto | `audio-quarto` |
| Varanda | `audio-varanda` |
| Escritório | `audio-escritorio` |

`git pull` não troca esse estado. Não copie o `bridge/config.json` de uma sala para outra: os MACs e o nome do bridge são daquela unidade.

## Segurança

Não há login nesta versão. A API só aceita alvos conhecidos: um MAC, um hostname no formato certo, ou reiniciar aplicação, Bluetooth ou Sendspin. Não existe endpoint que receba um comando. Segredos do Music Assistant ficam no volume do bridge e são ocultados nas respostas da nossa API. Para exigir um token depois, defina `API_TOKEN` no `.env` e envie `Authorization: Bearer`.

## Desenvolvimento

```bash
python3 -m venv .venv
.venv/bin/pip install -r api/requirements.txt pytest
.venv/bin/pytest
```

No Ubuntu 24.04, `python3` é o 3.12. Os testes simulam o bridge. Eles não pareiam uma caixa real.
