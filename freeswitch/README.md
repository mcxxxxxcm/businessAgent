# FreeSWITCH 配置模板

## 安装 FreeSWITCH

### Linux (Ubuntu/Debian)
```bash
# 添加仓库
wget -O - https://files.freeswitch.org/repo/deb/debian-release/freeswitch_archive_g0.pub | apt-key add -
echo "deb http://files.freeswitch.org/repo/deb/debian-release/ `lsb_release -sc` main" > /etc/apt/sources.list.d/freeswitch.list
apt-get update
apt-get install -y freeswitch freeswitch-mod-audio-stream freeswitch-mod-esl
```

### Docker (推荐)
```bash
docker run -d \
  --name freeswitch \
  -p 5060:5060/udp \
  -p 5060:5060/tcp \
  -p 8021:8021 \
  -v ./freeswitch/conf:/etc/freeswitch \
  drachtio/freeswitch
```

## 配置音频流到语音网关

### 方法1: mod_audio_stream (推荐)

在 `conf/autoload_modules.conf.xml` 中启用:
```xml
<load module="mod_audio_stream"/>
```

在 `conf/dialplan/default.xml` 中添加:
```xml
<extension name="ai_agent">
  <condition field="destination_number" expression="^9000$">
    <action application="answer"/>
    <action application="audio_stream" data="ws://127.0.0.1:8765"/>
  </condition>
</extension>
```

### 方法2: ESL Outbound

在 `conf/autoload_modules.conf.xml` 中确保ESL启用:
```xml
<load module="mod_event_socket"/>
```

在 `conf/autoload_configs/event_socket.conf.xml` 中:
```xml
<configuration name="event_socket.conf" description="Socket Client">
  <settings>
    <param name="nat-map" value="false"/>
    <param name="listen-ip" value="127.0.0.1"/>
    <param name="listen-port" value="8021"/>
    <param name="password" value="ClueCon"/>
    <param name="apply-inbound-acl" value="loopback.auto"/>
  </settings>
</configuration>
```

在 dialplan 中:
```xml
<extension name="ai_agent_esl">
  <condition field="destination_number" expression="^9001$">
    <action application="answer"/>
    <action application="socket" data="127.0.0.1:8765 async full"/>
  </condition>
</extension>
```

## 接入真实电话线路

### SIP Trunk 配置

在 `conf/sip_profiles/external.xml` 中添加SIP网关:
```xml
<gateway name="your_sip_provider">
  <param name="realm" value="sip.provider.com"/>
  <param name="username" value="your_username"/>
  <param name="password" value="your_password"/>
  <param name="from-domain" value="sip.provider.com"/>
  <param name="expire-seconds" value="600"/>
  <param name="register" value="true"/>
</gateway>
```

### DID 入呼路由

将外呼号码路由到AI Agent:
```xml
<extension name="inbound_ai">
  <condition field="destination_number" expression="^4001234567$">
    <action application="answer"/>
    <action application="audio_stream" data="ws://127.0.0.1:8765"/>
  </condition>
</extension>
```

## 验证

```bash
# 拨打9000分机测试
fs_cli -x "originate sofia/internal/9000%localhost &echo"

# 查看ESL连接
fs_cli -x "show connections"
```
