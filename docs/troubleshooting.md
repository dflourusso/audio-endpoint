# Diagnóstico

Rode estes comandos na placa, por SSH. A interface em Manutenção também mostra os logs do bridge, do agente, do Bluetooth e do Avahi.

## mDNS não funciona

`audio-sala.local` depende do Avahi no host, não do container.

```bash
hostname
systemctl status avahi-daemon --no-pager
avahi-browse -art | head
```

Confira se o celular está na mesma rede e se o roteador não bloqueia multicast entre Wi-Fi e cabo. Depois de mudar o hostname, o anúncio antigo pode levar um minuto para sumir. `ping audio-sala.local` a partir de outro Linux, ou `dns-sd -Q audio-sala.local` no Mac.

Se o serviço estiver parado: `sudo systemctl restart avahi-daemon`.

## Bluetooth não aparece

```bash
rfkill list
bluetoothctl show
lsusb
```

`bluetoothctl show` precisa listar um controlador `Powered: yes`. Sem controlador, a busca da interface falha porque o bridge não tem adaptador. No Orange Pi Zero 2W o rádio de bordo sobe com o BlueZ; um `rfkill block` deixa a busca vazia.

`sudo systemctl restart bluetooth` e, em seguida, reinicie o Sendspin pela interface.

## A caixa não conecta

A caixa precisa estar em modo de pareamento na primeira vez. Depois disso o vínculo fica em `/var/lib/bluetooth` e na frota do bridge.

```bash
bluetoothctl info AA:BB:CC:DD:EE:FF
docker logs --tail 80 sendspin-bridge
```

`Paired: yes` com `Connected: no` é uma caixa conhecida e desligada, ou fora de alcance. Ligue a caixa e use Conectar. Se o pareamento quebrou, Esquecer e parear de novo.

Duas coisas diferentes: pareada no BlueZ, e registrada em `BLUETOOTH_DEVICES`. Sem o registro, a caixa conecta e o Music Assistant não ganha player.

## Sendspin não é descoberto

O bridge precisa da rede do host e do Avahi do host.

```bash
docker ps --filter name=sendspin-bridge
docker logs --tail 80 sendspin-bridge
avahi-browse -rt _sendspin._tcp
```

O log deve mostrar o player escutando e o anúncio mDNS. `SENDSPIN_SERVER` no `.env` fica `auto`, a menos que você queira apontar para o IP do Music Assistant. Só pode haver um servidor Sendspin do Music Assistant na LAN.

Confira também se o container montou o D-Bus: o diretório `/var/run/dbus` existe no host e o `bluetoothd` está ativo.

## Music Assistant não encontra o player

No Music Assistant, o provider Sendspin tem de estar ligado. O player só existe depois que a caixa está na frota do bridge. Um pareamento só no BlueZ não basta.

A página Music Assistant desta interface mostra `ma_connected`. Se estiver aguardando, o bridge subiu e o servidor ainda não abriu a sessão. Reinicie o provider Sendspin no Music Assistant e espere cerca de um minuto.

O nome exibido é o nome do player mais o `BRIDGE_NAME`. Mudar o hostname pela nossa interface atualiza esse sufixo.

## Spotify não lista a caixa

O Orange Pi não anuncia Spotify Connect. Quem publica o dispositivo é o plugin Spotify Connect do Music Assistant. Marque o player do bridge nesse plugin. Se o Music Assistant estiver desligado, a caixa some do aplicativo do Spotify.

Contas Premium criadas depois de dezembro de 2024 devem usar o motor Spotify Soloist, não o go-librespot.

## Problemas de áudio

Caixa conectada e sem som quase sempre é PipeWire sem sink Bluetooth.

```bash
sudo -u audioendpoint XDG_RUNTIME_DIR=/run/user/$(id -u audioendpoint) wpctl status
docker logs --tail 80 sendspin-bridge
```

O `install.sh` sobe PipeWire, PipeWire Pulse e WirePlumber no usuário `audioendpoint`, e o `.env` guarda o UID desse usuário. Se o UID do socket não bater com `AUDIO_UID`, o bridge conecta no Bluetooth e não abre o sink.

Esta versão não manda o áudio do Sendspin para USB, P2 ou HDMI. Essas saídas aparecem na página Áudio só para mostrar o que a placa tem.

Volume muito baixo ou atraso grande se ajusta no bridge, no campo `static_delay_ms` da caixa. O padrão gravado no pareamento é 300 ms.

## Problemas de Docker

```bash
docker compose --project-directory /opt/audio-endpoint ps
docker compose --project-directory /opt/audio-endpoint logs --tail 80
```

Os dois serviços usam `restart: unless-stopped` e a rede do host. Se a API não abrir na porta 80, veja se outro processo já escuta essa porta. O bridge usa 8080 e, por caixa, portas a partir de 8928.

Uma atualização interrompida não apaga `bridge/` nem `.env`. O estado fica em `/opt/audio-endpoint/data/update-status.json`. Para voltar:

```bash
cd /opt/audio-endpoint
git checkout <commit-anterior>
docker compose up -d --build
```

## O cartão enche de log

O Compose guarda no máximo 10 MB por arquivo e três arquivos por container. O instalador limita o journald a 50 MB. A aplicação não grava log próprio no cartão; configuração só é escrita quando alguém muda hostname, pareamento ou uma atualização começa.

Se o cartão ainda encher:

```bash
docker system df
sudo journalctl --disk-usage
```

`docker image prune` remove imagens antigas. Não apague o volume `bridge/` nem `/var/lib/bluetooth`.

## A placa ficou em audio-setup

Isso é o modo de configuração. Abra `http://192.168.4.1` no celular que entrou na rede `audio-setup` e escolha o Wi-Fi de casa. A senha precisa ter pelo menos 8 caracteres, ou ficar em branco se a rede for aberta.

Se a senha estiver errada, a página mostra o erro e a placa volta para `audio-setup`. Buscar redes de novo desliga o rádio por alguns segundos; o celular pode cair da rede e precisar entrar outra vez.

Se duas placas anunciarem `audio-setup` ao mesmo tempo, desligue uma e configure a outra. O endereço `192.168.4.1` é o mesmo nas duas.

Depois de um pico de energia a placa espera cerca de 3 minutos pelas redes salvas antes de abrir o ponto de acesso. Se ninguém se conectar, ela desliga o ponto de acesso depois de 5 minutos e tenta as redes salvas de novo. Uma rede que só demorou a voltar não deixa a placa presa no ponto de acesso.

