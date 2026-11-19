#!/usr/bin/env python3
"""MikrotikAlignFree - безплатен инструмент за насочване на MikroTik антени.

Само стандартна библиотека на Python (3.8+, работи на Windows 7-11, Linux, macOS).
Стартира локален уеб сървър на 127.0.0.1:8080 и показва:
  * жив сигнал от антената (RouterOS API), пик, звукова индикация;
  * сигнал от двете страни - от втора антена през самата връзка или през другия
    компютър (синхронизация по MQTT);
  * тест на скоростта (/tool/bandwidth-test, изпълнява се от самата антена);
  * план на връзката: разстояние, азимут, наклон, зона на Френел, автоматично
    магнитно отклонение (модел WMM2025, изчислява се офлайн).
Не е официален продукт на MikroTik.
"""
import hashlib
import json
import math
import os
import random
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP_VERSION = "0.5.0"


def app_dir():
    """Папка за конфигурацията: до програмата, а ако е недостъпна за запис - в профила."""
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    if getattr(sys, "frozen", False) and os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA", base), "MikrotikAlignFree")
    os.makedirs(base, exist_ok=True)
    return base


BASE = app_dir()
CFG_PATH = os.path.join(BASE, "mikrotikalignfree_config.json")

DEFAULT_CFG = {
    "router_host": "192.168.88.1",
    "router_port": 8728,
    "router_ssl": False,
    "router_user": "admin",
    "router_password": "",
    "interface": "",
    "poll_ms": 500,
    "demo": False,
    "http_port": 8080,
    # втора антена (другата страна на връзката, достъпна през самата връзка)
    "router2_enabled": False,
    "router2_host": "",
    "router2_port": 8728,
    "router2_ssl": False,
    "router2_user": "admin",
    "router2_password": "",
    # тест на скоростта - данни за btest сървъра на отсрещната антена
    "btest_user": "",
    "btest_password": "",
    # синхронизация между два компютъра
    "mqtt_enabled": False,
    "mqtt_brokers": "mqtts://broker.emqx.io:8883, mqtts://broker.hivemq.com:8883",
    "room": "",
    "side": "a",
    "lang": "en",
}
SECRET_KEYS = [k for k in DEFAULT_CFG if k.endswith("password")]
CFG_VERSION = [0]


# --------------------------------------------------------------------------- конфигурация
def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)
    try:
        os.chmod(CFG_PATH, 0o600)
    except OSError:
        pass


def clean_room(value):
    value = str(value).strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{8,40}", value) else ""


# --------------------------------------------------------------------------- език (en по подразбиране / bg)
LANG = [load_cfg().get("lang", "en")]
I18N = json.loads(r'''{"page":{"Насочване":"Aiming","План и карта":"Plan and map","Настройка на антената":"Antenna setup","Отчет":"Report","Инструкции":"Instructions","Тема: автоматична / тъмна / светла":"Theme: automatic / dark / light","Настройки":"Settings","Спира програмата":"Stops the program","Изход":"Exit","Този лаптоп е при:":"This laptop is at:","Вие сте при точка A":"You are at point A","А":"A","Вие сте при точка B":"You are at point B","Б":"B","Казва на другия компютър да не пипа антената, докато насочвате":"Tells the other computer not to touch the antenna while you are aiming","Аз насочвам":"I am aiming","Превключва: изключен / тон / говор (клавиш M)":"Cycles: off / tone / voice (key M)","Звук: изкл.":"Sound: off","По кой сигнал се води индикацията":"Which signal drives the indicator","по моя сигнал":"by my signal","по сигнала на другата страна":"by the signal of the other side","по по-слабия от двата":"by the weaker of the two","Нулира пика (клавиш P)":"Resets the peak (key P)","Нулирай пик":"Reset peak","Записва сигнала всяка секунда за CSV":"Logs the signal every second for a CSV file","Запис: изкл.":"Recording: off","Запазва текущата стойност като точка за отчета (клавиш S)":"Saves the current value as a point for the report (key S)","Запази точка":"Save point","Отваря инструкциите":"Opens the instructions","Помощ":"Help","потребител":"username","парола":"password","Свържи":"Connect","Компютърът трябва да е в същата мрежа като антената. Помощник за мрежата има в „Настройка на антената“.":"The computer must be on the same network as the antenna. A network helper is available in “Antenna setup”.","В RouterOS трябва да е включена API услугата (порт 8728):":"The API service must be enabled in RouterOS (port 8728):","Отделен потребител само с права":"A separate user with only the rights","създава „Настройка на антената → Допълнително“.":"is created by “Antenna setup → Advanced”.","Към настройките":"Go to Settings","Търси по IP адреси":"Search by IP addresses","Настрой лаптопа автоматично":"Set up the laptop automatically","Пробвай в демо режим":"Try demo mode","Другата страна насочва в момента - не пипайте антената!":"The other side is aiming right now - do not touch the antenna!","ТУК":"HERE","dB от пика":"dB from peak","ОТСРЕЩА":"FAR END","Разговор с другия компютър":"Chat with the other computer","Още няма съобщения.":"No messages yet.","Съобщение до другия компютър...":"Message to the other computer...","Изпрати":"Send","Задръжте (или клавиш Интервал) и говорете":"Hold (or the Space key) and speak","🎤 Задръж и говори":"🎤 Hold and talk","Бързите бутони и текстът стигат отсреща веднага; ✓ значи, че другият компютър го е получил. За глас задръжте бутона 🎤 (или клавиша Интервал, когато не пишете в поле), говорете и пуснете - отсреща се чува след около секунда. Първия път браузърът пита за микрофона - натиснете „Разреши“.":"Quick buttons and text reach the other side immediately; ✓ means the other computer received it. For voice, hold the 🎤 button (or the Space key when you are not typing in a field), speak and release - it is heard at the other end after about a second. The first time, the browser asks for the microphone - click “Allow”.","Чети на глас получените текстови съобщения":"Read received text messages aloud","История на сигнала":"Signal history","(последните 3 минути)":"(last 3 minutes)","тук приема ·":"received here ·","отсреща приема · пунктир = пик тук":"received at the far end · dashed = peak here","Целта за моята страна":"The target for my side","Изчислете плана в „План и карта“, за да видите целевия азимут и наклон.":"Calculate the plan in “Plan and map” to see the target azimuth and tilt.","Компасът ми показва сега, °":"My compass shows now, °","Инклинометърът показва, °":"The inclinometer shows, °","Свързани устройства (регистрационна таблица)":"Connected devices (registration table)","Няма свързани устройства в регистрационната таблица.":"No connected devices in the registration table.","Точка A":"Point A","Име":"Name","Координати (или линк от Google Maps)":"Coordinates (or a Google Maps link)","Посочи на картата":"Pick on the map","Тук съм":"I am here","Терен, н.в. (m) - празно = автоматично":"Ground elevation (m a.s.l.) - empty = automatic","авто":"auto","Антена над терена, m":"Antenna above ground, m","Модел":"Model","Усилване, dBi":"Gain, dBi","TX мощност, dBm":"TX power, dBm","Ширина на лъча, ° (празно = от модела)":"Beam width, ° (empty = from the model)","Точка B":"Point B","Посочете двете точки (на картата, с „Тук съм“ или с координати). Така екранът „Насочване“ показва посоката и очаквания сигнал за вашата страна. Данните за моделите са ориентировъчни - проверете ги в datasheet на производителя.":"Set both points (on the map, with “I am here”, or with coordinates). The Aiming screen then shows the direction and the expected signal for your side. The model data is approximate - check it in the manufacturer datasheet.","Връзка":"Link","Честота, MHz":"Frequency, MHz","Ширина на канала, MHz":"Channel width, MHz","Допълнителни загуби, dB":"Additional losses, dB","Дървета/сгради по трасето, m":"Trees/buildings along the path, m","Пречупване в атмосферата":"Atmospheric refraction","Стандартно (k=1.33)":"Standard (k=1.33)","Без пречупване (k=1)":"No refraction (k=1)","Силно (k=2)":"Strong (k=2)","Отрицателно, мъгла (k=0.67)":"Negative, fog (k=0.67)","Магнитно отклонение, ° (изток +)":"Magnetic declination, ° (east +)","Автоматично по модела WMM2025 (работи без интернет)":"Automatic from the WMM2025 model (works without internet)","Изчисли":"Calculate","Изтегля релефа отново и преизчислява":"Downloads the terrain again and recalculates","Обнови релефа":"Refresh terrain","Щракване върху картата задава:":"Clicking on the map sets:","точка A":"point A","точка B":"point B","Карта":"Map","Релеф":"Terrain","Сателит":"Satellite","Задава избраната точка по местоположението на устройството":"Sets the selected point from the location of the device","Моята позиция":"My position","Покажи трасето":"Show the path","Картата се зарежда...":"Loading the map...","Маркерите се местят с влачене. Сините/оранжевите сектори показват посоката и ширината на лъча. Подложките на картата изискват интернет.":"Markers can be dragged. The blue/orange sectors show the direction and width of the beam. Map tiles need internet.","Проекти":"Projects","Име на проект":"Project name","напр. Клиент - Склад":"e.g. Client - Warehouse","Запазени":"Saved","Запази":"Save","Зареди":"Load","Изтрий":"Delete","Експорт (JSON)":"Export (JSON)","Импорт":"Import","Сподели":"Share","Изпраща въведените данни (точки, височини, модели, честота) на другия компютър през синхронизацията, за да имате еднакви числа от двете страни.":"Sends the entered data (points, heights, models, frequency) to the other computer through the synchronization, so that both sides have the same numbers.","Изпрати плана на другия компютър":"Send the plan to the other computer","Въведете точките (или ги посочете на картата) и натиснете „Изчисли“. Релефът се взема от Open-Meteo (Copernicus DEM, 90 m) и изисква интернет. Той не включва дървета и сгради - ползвайте полето „Дървета/сгради по трасето“.":"Enter the points (or pick them on the map) and click “Calculate”. The terrain comes from Open-Meteo (Copernicus DEM, 90 m) and needs internet. It does not include trees and buildings - use the field “Trees/buildings along the path”.","терен (с кривината на Земята) ·":"terrain (with Earth curvature) ·","дървета/сгради ·":"trees/buildings ·","линия на видимост ·":"line of sight ·","1-ва зона на Френел ·":"1st Fresnel zone ·","60% от зоната":"60% of the zone","Настройка на антената и тест на скоростта":"Antenna setup and speed test","Тук програмата настройва антената вместо вас - без WinBox и без команди. Правете го у дома, на бюрото, за всяка антена поотделно (с този или с другия компютър). Отнема 1-3 минути на антена. Следвайте стъпките отгоре надолу.":"Here the program configures the antenna for you - without WinBox and without commands. Do it at home, on the desk, for each antenna separately (with this or the other computer). It takes 1-3 minutes per antenna. Follow the steps from top to bottom.","Свържете антената":"Connect the antenna","Порт":"Port","на адаптера → към антената. Порт":"on the adapter → to the antenna. Port","→ към този компютър. Адаптерът - в контакта.":"→ to this computer. The adapter goes into the power outlet.","Изчакайте около минута, докато антената тръгне. Програмата я намира сама по кабела (както „Neighbors“ в WinBox) - не пишете IP адреси.":"Wait about a minute for the antenna to boot. The program finds it by itself over the cable (like “Neighbors” in WinBox) - do not type IP addresses.","Ако програмата поиска парола - тя е на етикета на антената (":"If the program asks for a password - it is on the label of the antenna (","). Въвежда се в раздел „Насочване“, най-отгоре.":"). Enter it in the “Aiming” tab, at the top.","Когато горе пише „Антена ✓“, натиснете „Провери антената“. Нищо не се променя - програмата само чете какво има на антената.":"When it says “Antenna ✓” at the top, click “Check the antenna”. Nothing is changed - the program only reads what is on the antenna.","Антена":"Antenna","Основна (към която е свързан компютърът)":"Primary (the one the computer is connected to)","Втора (другата страна)":"Secondary (the other side)","Провери антената":"Check the antenna","Коя е тази антена":"Which antenna is this","Едната антена е":"One antenna is the","точка за достъп":"access point",", другата -":", the other is the","клиент":"client",". Няма значение коя коя е, но обикновено точката за достъп е там, откъдето идва интернетът. Изборът идва от „План и карта“ (кой компютър е А и кой Б) - можете да го смените тук.":". It does not matter which is which, but usually the access point is where the internet comes from. The choice comes from “Plan and map” (which computer is A and which is B) - you can change it here.","А - Точка за достъп":"A - Access point","Б - Клиент":"B - Client","Данни за връзката":"Link details","Попълнено е с подходящи стойности - обикновено трябва само да измислите парола.":"Filled in with suitable values - usually you only need to make up a password.","Името на връзката, паролата и честотата трябва да са еднакви на двете антени.":"The link name, the password and the frequency must be the same on both antennas.","Име на антената":"Antenna name","Вижда се в програмата и в WinBox. Латиница, без интервали.":"Shown in the program and in WinBox. Latin letters, no spaces.","Име на връзката (SSID)":"Link name (SSID)","Еднакво на двете антени.":"The same on both antennas.","Парола на връзката (8-63 знака)":"Link password (8-63 characters)","Покажи":"Show","Измисли нова":"Make up a new one","Латиница и цифри.":"Latin letters and digits.","Еднаква на двете антени - запишете си я.":"The same on both antennas - write it down.","Това не е паролата на admin - тя не се сменя.":"This is not the admin password - it is not changed.","От плана":"From the plan","Еднаква на двете антени.":"The same on both antennas.","Започнете с 20 - по-стабилно е за насочване.":"Start with 20 - it is more stable for aiming.","IP адрес на антената":"IP address of the antenna","Различен за двете антени (обикновено .2 и .3). Ако антената ще е в домашна мрежа и искате да я стигате по IP, сложете свободен адрес от нея.":"Different for the two antennas (usually .2 and .3). If the antenna will be in a home network and you want to reach it by IP, use a free address from that network.","Шлюз (по желание)":"Gateway (optional)","празно":"empty","Само ако антената трябва да излиза в интернет (за обновяване на RouterOS). Обикновено е адресът на рутера.":"Only if the antenna must reach the internet (to update RouterOS). Usually it is the address of the router.","Допълнителни настройки":"Advanced settings","Пакет":"Package","wifi (RouterOS 7, ax модели)":"wifi (RouterOS 7, ax models)","wireless (класическо меню)":"wireless (classic menu)","Безжичен интерфейс":"Wireless interface","Кабелен порт":"Cable port","Обхват":"Band","Държава (латиница)":"Country (Latin letters)","Nv2 (само пакет wireless и MikroTik от двете страни)":"Nv2 (only package wireless and MikroTik on both sides)","Изчисти всички стари настройки преди това":"Clear all old settings first","Не е задължително: програмата сама маха фабричните неща, които пречат на моста (раздаване на адреси и др.). Изберете го, ако антената е настройвана преди и искате да започнете на чисто. Паролата на admin остава.":"Not mandatory: the program removes the factory settings that get in the way of the bridge (address assignment etc.) by itself. Select it if the antenna was configured before and you want to start clean. The admin password stays.","Преглед и прилагане":"Review and apply","Натиснете":"Click","„Покажи какво ще се промени“":"“Show what will change”","и прочетете списъка.":"and read the list.","„Приложи настройките“":"“Apply the settings”",". Програмата първо прави резервно копие на сегашните настройки в самата антена, после записва новите.":". The program first makes a backup of the current settings inside the antenna itself, then writes the new ones.","Връзката с антената за момент може да прекъсне - това е нормално. Не изключвайте антената, докато не излезе резултат.":"The connection to the antenna may drop for a moment - this is normal. Do not switch off the antenna until the result appears.","Внимание:":"Attention:","промяната на SSID, парола, честота или IP може да прекъсне връзката с антената. Командите са генерирани по документацията на RouterOS и не са проверявани с всички версии - прегледайте ги внимателно.":"changing the SSID, password, frequency or IP may break the connection to the antenna. The commands are generated from the RouterOS documentation and are not verified on all versions - review them carefully.","Тук ще се покажат командите...":"The commands will appear here...","Направи резервно копие преди промяната (препоръчително)":"Make a backup before the change (recommended)","Разбирам, че връзката може да прекъсне":"I understand that the connection may drop","Покажи какво ще се промени":"Show what will change","Приложи настройките":"Apply the settings","Само резервно копие":"Backup only","Копирай командите":"Copy the commands","Другата антена и проба на бюрото":"The other antenna and a test on the desk","Свържете другата антена (към този или към другия компютър) и повторете стъпки 1-4: изберете другата роля,":"Connect the other antenna (to this or to the other computer) and repeat steps 1-4: choose the other role,","същото име на връзката, същата парола и същата честота":"the same link name, the same password and the same frequency","и друг IP адрес.":"and a different IP address.","Включете двете антени на бюрото, на 2-3 m една от друга и":"Switch on both antennas on the desk, 2-3 m from each other and","обърнати встрани":"turned sideways","- иначе сигналът е твърде силен.":"- otherwise the signal is too strong.","До минута в раздел „Насочване“ трябва да се появи сигнал. На честоти 5500-5700 MHz (DFS) точката за достъп изчаква 1 минута преди да излъчва (на 5600-5650 MHz - до 10 минути).":"Within a minute a signal should appear in the “Aiming” tab. On frequencies 5500-5700 MHz (DFS) the access point waits 1 minute before transmitting (on 5600-5650 MHz - up to 10 minutes).","След това пуснете теста на скоростта отдолу - така сте сигурни, че всичко работи, преди да качите антените.":"Then run the speed test below - this way you are sure that everything works before you put the antennas up.","Тест на скоростта между антените":"Speed test between the antennas","Антената, свързана с този компютър, праща данни към другата и едновременно приема от нея (Bandwidth Test на MikroTik). Пуснете го, когато антените вече се виждат - на бюрото за проба и после на място, след насочването. Интернет и компютрите не участват - мери се само радиовръзката.":"The antenna connected to this computer sends data to the other one and at the same time receives from it (MikroTik Bandwidth Test). Run it when the antennas can already see each other - on the desk as a trial and then on site, after aiming. The internet and the computers are not involved - only the radio link is measured.","Тестът запълва връзката докрай":"The test fills the link completely","- не го пускайте върху работеща мрежа.":"- do not run it on a working network.","IP адрес на другата антена":"IP address of the other antenna","напр. 192.168.88.3":"e.g. 192.168.88.3","Вид на теста":"Test type","TCP - като истински трафик":"TCP - like real traffic","UDP - колко най-много може връзката":"UDP - the maximum the link can do","Започнете с TCP.":"Start with TCP.","Посока":"Direction","и двете":"both","изпращане":"send","приемане":"receive","Продължителност":"Duration","5 секунди":"5 seconds","10 секунди":"10 seconds","20 секунди":"20 seconds","30 секунди":"30 seconds","Парола на другата антена (само ако е нужна)":"Password of the other antenna (only if needed)","не е нужна":"not needed","Не е нужна, ако двете антени са настроени от програмата с една и съща парола на връзката. Иначе - паролата на admin от етикета ѝ.":"Not needed if both antennas were configured by the program with the same link password. Otherwise - the admin password from its label.","Пусни теста":"Run the test","Спри":"Stop","Изпращане сега / средно, Mbps":"Sending now / average, Mbps","Приемане сега / средно, Mbps":"Receiving now / average, Mbps","Допълнително: потребител само за четене, мрежа на компютъра, полезни команди":"Advanced: read-only user, computer network, useful commands","Подготви антената за програмата":"Prepare the antenna for the program","Създава потребител само с права":"Creates a user with only the rights","(за четене на сигнала и теста на скоростта), включва API услугата и bandwidth-server. Изпълнява се с данните от „Настройки“ (например администраторски).":"(to read the signal and for the speed test), enables the API service and the bandwidth-server. It runs with the credentials from “Settings” (for example administrator).","Нов потребител":"New user","Парола (поне 8 знака)":"Password (at least 8 characters)","Измисли":"Make up one","Разрешен адрес за API (по желание)":"Allowed address for the API (optional)","напр. 192.168.88.0/24":"e.g. 192.168.88.0/24","Включи bandwidth-server (за теста на скоростта)":"Enable the bandwidth-server (for the speed test)","Приложи":"Apply","Ползвай този потребител в програмата":"Use this user in the program","Мрежа на компютъра (Windows)":"Computer network (Windows)","За да говори компютърът с антената, трябва да е в същата мрежа. Тук можете временно да зададете статичен адрес и после да върнете автоматичния. Кабелната карта получава временен IP; бутонът „Върни на автоматичен IP“ го премахва. Windows ще поиска потвърждение (UAC).":"For the computer to talk to the antenna, it must be on the same network. Here you can temporarily set a static address and later return to automatic. The wired adapter gets a temporary IP; the button “Back to automatic IP” removes it. Windows will ask for confirmation (UAC).","Мрежов адаптер":"Network adapter","напр. Ethernet":"e.g. Ethernet","IP адрес на компютъра":"IP address of the computer","Маска":"Mask","Настрой компютъра автоматично":"Set the computer automatically","Върни на автоматичен IP":"Back to automatic IP","Полезни команди за RouterOS (копирайте в терминала на WinBox)":"Useful RouterOS commands (copy them into the WinBox terminal)","Протокол от насочването":"Aiming report","Събира плана, запазените точки и последния тест на скоростта в страница за печат или запис като PDF (печат → „Запази като PDF“).":"Collects the plan, the saved points and the last speed test into a page for printing or saving as PDF (print → “Save as PDF”).","Клиент":"Client","Обект / адрес":"Site / address","Изпълнител":"Technician","Дата":"Date","Бележки":"Notes","Запазени точки на измерване":"Saved measurement points","Отвори за печат":"Open for printing","Свали като HTML":"Download as HTML","Свали записа (CSV)":"Download the recording (CSV)","Изчисти точките":"Clear the points","Антена, към която е свързан този компютър":"Antenna this computer is connected to","Адрес":"Address","API порт (8728 или 8729 за SSL)":"API port (8728, or 8729 for SSL)","Потребител":"Username","Парола (празно = без промяна)":"Password (empty = no change)","Само интерфейс (по избор)":"Interface only (optional)","напр. wifi1":"e.g. wifi1","Интервал на опресняване, ms":"Refresh interval, ms","Използвай API-SSL":"Use API-SSL","Демо режим (симулиран сигнал, без антена)":"Demo mode (simulated signal, no antenna)","Тествай връзката":"Test the connection","Търси антена в мрежата":"Search for the antenna on the network","Втора антена (другата страна, достъпна през самата връзка)":"Second antenna (the other side, reachable through the link itself)","Така един компютър вижда сигнала и от двете страни, без втори лаптоп.":"This way one computer sees the signal from both sides, without a second laptop.","Включи втората антена":"Enable the second antenna","напр. 192.168.88.2":"e.g. 192.168.88.2","API порт":"API port","Тест на скоростта (данни за сървъра на отсрещната антена)":"Speed test (data for the server on the far antenna)","Потребител (по избор)":"Username (optional)","Синхронизация и разговор между два компютъра":"Synchronization and chat between two computers","Всеки компютър е свързан със своята антена и праща само стойностите на сигнала през публичен MQTT брокер. Стойностите не са криптирани, затова кодът е дълъг и случаен - споделяйте го само с колегата си.":"Each computer is connected to its own antenna and sends only the signal values through a public MQTT broker. The values are not encrypted, so the code is long and random - share it only with your colleague.","Включи синхронизацията":"Enable synchronization","Този компютър е страна":"This computer is side","Код на стаята (еднакъв на двата компютъра)":"Room code (the same on both computers)","Брокери (по ред на опити)":"Brokers (tried in order)","Нов код":"New code","Копирай кода":"Copy the code","След смяна на кода запишете настройките и го въведете на другия компютър.":"After changing the code, save the settings and enter it on the other computer.","Външен вид и звук":"Appearance and sound","Тема":"Theme","Автоматична":"Automatic","Светла":"Light","Тъмна":"Dark","Говор: на всеки, s":"Voice: every, s","Ниска/висока честота на тона, Hz":"Low/high tone frequency, Hz","Запази и свържи":"Save and connect","Бързи клавиши":"Hotkeys","Интервал":"Space","звук (изкл./тон/говор) ·":"sound (off/tone/voice) ·","нулира пика ·":"resets the peak ·","запазва точка ·":"saves a point ·","раздели":"tabs","MikrotikAlignFree е безплатна програма, не е официален продукт на MikroTik. Картите са © OpenStreetMap, OpenTopoMap, Esri. Библиотека Leaflet (BSD-2).":"MikrotikAlignFree is a free program, not an official MikroTik product. Maps are © OpenStreetMap, OpenTopoMap, Esri. Leaflet library (BSD-2).","MikrotikAlignFree v__VERSION__ · безплатна програма, не е официален продукт на MikroTik":"MikrotikAlignFree v__VERSION__ · free program, not an official MikroTik product","Програмата не отговаря.":"The program is not responding.","Антената не отговаря. Проверете адреса, кабела/PoE и дали компютърът е в същата мрежа (напр. 192.168.88.х).":"The antenna is not responding. Check the address, the cable/PoE and whether the computer is on the same network (e.g. 192.168.88.x).","Адресът отговаря, но API услугата е изключена или портът е друг (по подразбиране 8728). В RouterOS: /ip service enable api":"The address responds, but the API service is disabled or the port is different (default 8728). In RouterOS: /ip service enable api","Грешно потребителско име или парола (или потребителят няма права read и api).":"Wrong username or password (or the user does not have the read and api rights).","Няма маршрут до този адрес - компютърът е в друга мрежа. Ползвайте „Настройка на антената → Мрежа на компютъра“.":"No route to this address - the computer is on another network. Use “Antenna setup → Computer network”.","Грешка при SSL връзката. Опитайте порт 8728 без SSL или проверете сертификата на api-ssl.":"SSL connection error. Try port 8728 without SSL or check the api-ssl certificate.","Адресът не може да се разреши - въведете IP адрес.":"The address cannot be resolved - enter an IP address.","Връзката е прекъсната от антената.":"The connection was closed by the antenna.","Да спра ли програмата?":"Stop the program?","Програмата е спряна. Можете да затворите този раздел.":"The program has stopped. You can close this tab.","(втората антена)":"(the second antenna)","(другия компютър)":"(the other computer)","(TX сигнал от антената)":"(TX signal from the antenna)","Отличен сигнал":"Excellent signal","Много добър сигнал":"Very good signal","Добър сигнал":"Good signal","Приемлив сигнал - малък запас":"Acceptable signal - small margin","Слаб сигнал":"Weak signal","Много слаб сигнал":"Very weak signal","Звук:":"Sound:","изкл.":"off","тон":"tone","говор":"voice","минус":"minus","Записът има":"The recording has","измервания. Да го сваля ли като CSV?":"measurements. Download it as CSV?","Запис: вкл.":"Recording: on","време;тук_dBm;другата_страна_dBm;tx_скорост;rx_скорост":"time;here_dBm;other_side_dBm;tx_rate;rx_rate","Няма данни за сигнала.":"No signal data.","Име на точката (напр. „след затягане“):":"Name of the point (e.g. “after tightening”):","изключен":"disabled","работи":"running","неактивен":"inactive","Вериги Ch0 / Ch1":"Chains Ch0 / Ch1","SNR / шум":"SNR / noise","Режим":"Mode","Честота / ширина":"Frequency / width","Връзка от":"Connected for","Приема се отсреща":"Received at the far end","Скорост tx / rx":"Rate tx / rx","Режим / състояние":"Mode / state","Очакван по плана:":"Expected by the plan:","данни преди":"data age","Антена:":"Antenna:","свързана":"connected","Антена: иска парола":"Antenna: asks for a password","Антена: няма връзка":"Antenna: no connection","Втора антена:":"Second antenna:","Втора антена: няма връзка":"Second antenna: no connection","Интернет:":"Internet:","сървъра":"servers","Отсреща ✓":"Far end ✓","Отсреща: чака...":"Far end: waiting...","Синхронизация:":"Synchronization:","Нужно е действие - вижте съобщението над панелите.":"Action needed - see the message above the panels.","Антената е свързана, но няма свързан клиент в регистрационната таблица (проверете SSID, парола, честота и роли).":"The antenna is connected, but there is no connected client in the registration table (check the SSID, password, frequency and roles).","Чакам другия компютър... Пуснете програмата там със стая „":"Waiting for the other computer... Start the program there with room “","Няма данни отсреща. Включете „Синхронизация“ (втори компютър) или втората антена в „Настройки“.":"No data from the far end. Enable “Synchronization” (second computer) or the second antenna in “Settings”.","Интерфейс":"Interface","Сигнал":"Signal","Страна":"Side","Целеви азимут по компас":"Target compass azimuth","(географски":"(geographic","Целеви наклон":"Target tilt","Очакван сигнал":"Expected signal","Азимут":"Azimuth","По компас":"By compass","Наклон":"Tilt","Посоката съвпада (отклонение":"The direction matches (deviation","Завъртете":"Turn","надясно (по часовниковата стрелка)":"to the right (clockwise)","наляво":"to the left","Наклонът съвпада.":"The tilt matches.","Наклонете":"Tilt","нагоре":"up","надолу":"down","Намерих":"Found","антена":"antenna",", но иска парола.":", but it asks for a password.","Антената иска парола.":"The antenna asks for a password.","Записаната парола не става. Паролата е на етикета на антената (или тази, която сте сложили).":"The saved password is not accepted. The password is on the label of the antenna (or the one you set).","Няма връзка с антената (":"No connection to the antenna (","(техническо:":"(technical:","Виждам":"I can see","антени":"antennas","- изберете тази, която е":"- choose the one that is","с кабел към този компютър":"with a cable to this computer","· друга мрежа":"· other network","Не виждам антени по кабела (откриване MNDP). Изчакайте минута след включване или опитайте „Търси по IP адреси“.":"I cannot see antennas on the cable (MNDP discovery). Wait a minute after power-on or try “Search by IP addresses”.","Свързвам...":"Connecting...","/ethernet|lan|кабел/i.test(x))||ad.adapters[0];if(ad.adapters.length":"/ethernet|lan|кабел|cable/i.test(x))||ad.adapters[0];if(ad.adapters.length","Кой адаптер е с кабела към антената?":"Which adapter is connected by cable to the antenna?","Ще задам на адаптер „":"I will set adapter “","“ временен адрес":"” to the temporary address",", за да стигне до антената. Windows ще поиска потвърждение. Продължавам ли?":", so that it can reach the antenna. Windows will ask for confirmation. Continue?","Готово. Адресът е зададен - след няколко секунди връзката трябва да тръгне. „Върни на автоматичен IP“ е в „Настройка на антената → Допълнително“.":"Done. The address is set - after a few seconds the connection should come up. “Back to automatic IP” is in “Antenna setup → Advanced”.","Демо режимът е включен.":"Demo mode is on.","Аз насочвам ✓":"I am aiming ✓","Чуваш ли ме?":"Can you hear me?","Чакай":"Wait","Върти бавно":"Turn slowly","Расте ↑":"Rising ↑","Пада ↓":"Falling ↓","Върни назад":"Go back","Спри - пикът е тук":"Stop - the peak is here","Готово - твой ред":"Done - your turn","другият компютър е на линия":"the other computer is online","другият компютър не е на линия":"the other computer is offline","(изисква синхронизация - Настройки)":"(requires synchronization - Settings)","Аз":"Me","Записът е твърде дълъг. Говорете по-кратко (до 15 секунди).":"The recording is too long. Speak for a shorter time (up to 15 seconds).","Микрофонът не е достъпен:":"The microphone is not available:",". Разрешете го в браузъра (иконата вляво от адреса).":". Allow it in the browser (the icon to the left of the address).","Другата страна (":"The other side (",") изпрати план.":") sent a plan.","Приложи и изчисли":"Apply and calculate","Отхвърли":"Reject","Стартиране...":"Starting...","Тестът върви...":"The test is running...","Готово.":"Done.","Спрян.":"Stopped.","Грешка:":"Error:","Последни тестове:":"Latest tests:",": изпращане":": sending",", приемане":", receiving","Програмата не отговаря":"The program is not responding","Търся в мрежата...":"Searching the network...","Търся в мрежите":"Searching networks",".х":".x","- намерени:":"- found:","Не е намерена антена с отворен API порт (8728). Мрежи на компютъра:":"No antenna with an open API port (8728) was found. Networks of the computer:","не са открити":"none detected",". Ако компютърът не е в мрежата на антената, настройте го от „Настройка на антената → Мрежа на компютъра“.":". If the computer is not on the network of the antenna, set it up from “Antenna setup → Computer network”.","Намерени:":"Found:","- основна":"- primary","втора":"secondary","Адресът":"The address","е записан. Въведете потребител и парола в „Настройки“.":"has been saved. Enter the username and password in “Settings”.","стая:":"room:","(запазена)":"(saved)","Запазено.":"Saved.","Грешка при запис.":"Save error.","Проверявам...":"Checking...","✓ Свързване успешно:":"✓ Connected successfully:","Друго - въведете усилването":"Other - enter the gain","Друго":"Other","Насочени антени (точка-точка, CPE)":"Directional antennas (point-to-point, CPE)","mANTBox 15s - 15 dBi, сектор 120°":"mANTBox 15s - 15 dBi, 120° sector","Сектори и точки за достъп (AP)":"Sectors and access points (AP)","mANTBox 19s - 19 dBi, сектор 120°":"mANTBox 19s - 19 dBi, 120° sector","mANTBox 52 15s - 15 dBi, сектор 120°":"mANTBox 52 15s - 15 dBi, 120° sector","Сектор 90° - 17 dBi (друг)":"Sector 90° - 17 dBi (other)","Сектор 120° - 15 dBi (друг)":"Sector 120° - 15 dBi (other)","Всепосочна антена - 8 dBi (външна)":"Omnidirectional antenna - 8 dBi (outdoor)","Вътрешни и малки AP (всепосочни)":"Indoor and small APs (omnidirectional)","(от модела)":"(from the model)","2.4 GHz: до 20 dBm EIRP (ЕС).":"2.4 GHz: up to 20 dBm EIRP (EU).","5150-5250 MHz: до 23 dBm EIRP, само на закрито.":"5150-5250 MHz: up to 23 dBm EIRP, indoor only.","5250-5350 MHz: до 23 dBm EIRP, DFS и TPC.":"5250-5350 MHz: up to 23 dBm EIRP, DFS and TPC.","5470-5725 MHz: до 30 dBm EIRP, DFS и TPC (ЕС).":"5470-5725 MHz: up to 30 dBm EIRP, DFS and TPC (EU).","5725-5875 MHz: до 14 dBm EIRP (SRD) - проверете местните правила.":"5725-5875 MHz: up to 14 dBm EIRP (SRD) - check the local rules.","Честотата е с DFS: точката за достъп изчаква 1 минута преди да излъчва":"The frequency uses DFS: the access point waits 1 minute before transmitting","(в диапазона 5600-5650 MHz - 10 минути)":"(in the 5600-5650 MHz range - 10 minutes)","Изпратено.":"Sent.","Невалидни координати. Пример: 41.8485, 23.1117":"Invalid coordinates. Example: 41.8485, 23.1117","Точките съвпадат.":"The points coincide.","Моделът WMM2025 е валиден до 2030 г. - проверете отклонението.":"The WMM2025 model is valid until 2030 - check the declination.","Отклонението не може да се изчисли.":"The declination cannot be calculated.","Релефът не е наличен (":"The terrain is not available (",") - профилът е без релеф.":") - the profile has no terrain.","(авто)":"(auto)","чисто":"clear","приемливо (над 60% от зоната на Френел)":"acceptable (over 60% of the Fresnel zone)","частично затъмнение на зоната на Френел":"partial obstruction of the Fresnel zone","няма пряка видимост":"no line of sight","° нагоре":"° up","° надолу":"° down",", до ≈":", up to ≈","Mbps по канала (реално ≈":"Mbps on the channel (real ≈","под прага на MCS0 - връзката няма да се вдигне":"below the MCS0 threshold - the link will not come up","лъчът е широк - точната посока е без значение":"the beam is wide - the exact direction does not matter","насочете с точност ±":"aim with an accuracy of ±","° (под 1 dB загуба)":"° (under 1 dB loss)","азимут спрямо географския север":"azimuth relative to geographic north","(декл.":"(decl.","изток":"east","запад":"west","m терен +":"m terrain +","m н.в.":"m a.s.l.","Очакван сигнал тук":"Expected signal here","Ширина на лъча":"Beam width","Трасе":"Path","Видимост":"Visibility","Зона на Френел":"Fresnel zone","радиус":"radius","m в средата; 60% =":"m in the middle; 60% =","Кривина на Земята":"Earth curvature","m в средата (k=":"m in the middle (k=","Дървета/сгради":"Trees/buildings","m по трасето":"m along the path","Най-тясното място е на":"The narrowest point is at","m от A: свободни са":"m from A: clear for","% от зоната на Френел (нужни са поне 60%).":"% of the Fresnel zone (at least 60% is needed).","Профилът е без релеф - видимостта не е надеждна.":"The profile has no terrain - the visibility is not reliable.","Сигнал и скорост":"Signal and speed","Загуба в пространството":"Free-space loss","dB при":"dB at","Очакван при A / при B":"Expected at A / at B","Скорост при A":"Speed at A","Скорост при B":"Speed at B","EIRP надвишава ограничението (":"EIRP exceeds the limit (","dBm) - намалете мощността.":"dBm) - reduce the power.","Ориентировъчно: при правилно насочване измереният сигнал обикновено е в рамките на ±3-5 dB от очаквания. Скоростта е груба оценка за 2 потока (2x2) със запас 6 dB.":"Approximate: with correct aiming the measured signal is usually within ±3-5 dB of the expected one. The speed is a rough estimate for 2 streams (2x2) with a 6 dB margin.","km · терен":"km · terrain","m · линия":"m · line","m · свободно":"m · clear","% от зоната":"% of the zone","Библиотеката за карта не се зареди.":"The map library did not load.","Картата не може да се покаже:":"The map cannot be displayed:","Най-тясно място (":"Narrowest point (","% свободно)":"% clear)","Щракнете върху картата, за да зададете точка":"Click on the map to set point","Устройството не поддържа определяне на местоположение.":"The device does not support geolocation.","Местоположението не е достъпно:":"The location is not available:","Въведете име на проекта.":"Enter a project name.","Да изтрия ли „":"Delete “","Невалиден файл.":"Invalid file.","Чета...":"Reading...","Платка":"Board","Време на работа / процесор":"Uptime / CPU","Безжични интерфейси (":"Wireless interfaces (","не са намерени":"not found","Честота":"Frequency","Ширина":"Width","Състояние":"State","IP адреси":"IP addresses","няма":"none","Скрий":"Hide","Ако връзката е прекъснала, изчакайте минута и проверете в „Насочване“ (при смяна на IP - изберете антената отново от списъка).":"If the connection dropped, wait a minute and check in “Aiming” (if the IP was changed - select the antenna again from the list).","Да изпълня ли тези команди върху":"Run these commands on the","втората":"secondary","основната":"primary","антена?":"antenna?","✓ Новият потребител е записан в настройките на програмата.":"✓ The new user has been saved in the program settings.","✗ Грешка при запис.":"✗ Save error.","✓ Копието е записано в антената:":"✓ The backup is saved in the antenna:","и":"and","(Files в Winbox).":"(Files in WinBox).","Отбележете потвърждението под командите.":"Tick the confirmation below the commands.","Промяната може да прекъсне връзката. Да продължа ли?":"The change may break the connection. Continue?","✓ Готово (":"✓ Done (","Да сменя ли IP адреса на този компютър? Windows ще поиска потвърждение.":"Change the IP address of this computer? Windows will ask for confirmation.","Да върна ли автоматичния IP адрес?":"Restore the automatic IP address?","Включване на API услугата":"Enable the API service","Име на устройството":"Device name","IP адрес на управление (сменете интерфейса)":"Management IP address (change the interface)","Bandwidth-server (за теста на скоростта)":"Bandwidth-server (for the speed test)","Показване на свързаните устройства (wifi)":"Show connected devices (wifi)","Показване на свързаните устройства (wireless)":"Show connected devices (wireless)","Резервно копие":"Backup","Изключване на ненужни услуги":"Disable unneeded services","Копирай":"Copy","Време":"Time","Тук, dBm":"Here, dBm","Другата, dBm":"Other, dBm","Пик":"Peak","Още няма запазени точки. Използвайте „Запази точка“ при насочване.":"No saved points yet. Use “Save point” while aiming.","Да изчистя ли запазените точки?":"Clear the saved points?","Няма запис. Включете „Запис“ в лентата и насочвайте.":"No recording. Turn on “Recording” in the bar and aim.","Протокол -":"Report -","Протокол от насочване на безжична връзка":"Wireless link aiming report","Обект":"Site","План на връзката":"Link plan","Координати":"Coordinates","Височина на антената":"Antenna height","Азимут по компас":"Compass azimuth","Разстояние":"Distance","km, честота":"km, frequency","MHz, канал":"MHz, channel","MHz, видимост:":"MHz, visibility:","Измервания":"Measurements","Тест на скоростта":"Speed test","): изпращане":"): sending","Mbps, приемане":"Mbps, receiving","Създадено с AlignFree. Очакваните стойности са теоретични.":"Created with AlignFree. The expected values are theoretical.","Браузърът блокира новия прозорец. Разрешете го или свалете HTML.":"The browser blocked the new window. Allow it or download the HTML.","Антени по кабела:":"Antennas on the cable:","Още не виждам антени по кабела.":"I cannot see any antennas on the cable yet.","Намерена:":"Found:","Изчислете плана в „План и карта“ - тук ще се покажат вашите числа (азимут по компас, наклон, очакван сигнал), а инструкциите ще ги ползват.":"Calculate the plan in “Plan and map” - your numbers (compass azimuth, tilt, expected signal) will appear here and the instructions will use them.","Вашите числа":"Your numbers","(изчислено":"(calculated","Антена над терена":"Antenna above ground","Азимут (географски)":"Azimuth (geographic)",". Видимост:":". Visibility:",". Най-тясното място е на":". The narrowest point is at","(изчислете плана)":"(calculate the plan)","1. Как работи връзката":"1. How the link works","Двете антени и предават, и приемат - няма „приемник“ и „предавател“. Едната е":"Both antennas transmit and receive - there is no “receiver” and “transmitter”. One is the","(режим":"(mode","; в по-старото меню Wireless се казва":"; in the older Wireless menu it is called","), другата е":"), the other is the","Точката за достъп обикновено е при източника на интернет. В тази програма това е точка A, а клиентът е Б (можете да ги размените).":"The access point is usually at the internet source. In this program that is point A, and the client is B (you can swap them).","Връзката е прозрачен мост: каквото влезе в кабела от едната страна, излиза от другата. Рутерът и DHCP остават както са си.":"The link is a transparent bridge: whatever enters the cable on one side comes out on the other. The router and DHCP stay as they are.","Сигналът (dBm) е отрицателно число -":"The signal (dBm) is a negative number -","по-близо до нула е по-добре":"closer to zero is better",". Всяка страна измерва сигнала, с който чува другата, и за добра връзка трябва да е добър от":". Each side measures the signal with which it hears the other, and for a good link it must be good from","двете":"both","страни.":"sides.","2. Какво ви трябва":"2. What you need","Двете антени с PoE адаптерите им и екраниран (FTP) кабел за външна употреба.":"Both antennas with their PoE adapters and shielded (FTP) cable for outdoor use.","Стабилни стойки или мачти":"Stable mounts or masts",". Лъчът е широк около":". The beam is about","°, но мачта, която се люлее, пак сваля сигнала":"° wide, but a swaying mast still lowers the signal","Компас или телефон с компас, инклинометър (приложение), бинокъл, гаечни ключове, водозащитна лента.":"A compass or a phone with a compass, an inclinometer (app), binoculars, wrenches, waterproof tape.","Два компютъра с тази програма (или един, ако имате достъп до двете антени през връзката) и телефони с мобилен интернет (hotspot) за картата и синхронизацията.":"Two computers with this program (or one, if you have access to both antennas through the link) and phones with mobile internet (hotspot) for the map and the synchronization.","3. Настройване на антените (у дома, на бюрото)":"3. Setting up the antennas (at home, on the desk)","Най-лесно: раздел":"Easiest: the tab","„Настройка на антената“":"“Antenna setup”","- програмата настройва антената вместо вас, стъпка по стъпка, без WinBox и без команди, и после мери скоростта между антените. Отдолу е ръчният начин с WinBox - ако го предпочитате.":"- the program configures the antenna for you, step by step, without WinBox and without commands, and then measures the speed between the antennas. Below is the manual way with WinBox - if you prefer it.","Свържете се с WinBox (бутон Neighbors → по MAC адрес). Потребител":"Connect with WinBox (Neighbors button → by MAC address). Username",", паролата е на етикета на антената (Password); ако там няма парола - празно. Обновете RouterOS: System → Packages → Check for updates. По желание започнете на чисто: System → Reset Configuration → No Default Configuration (изтрива всички настройки; после се свързвате само по MAC). После в терминала на WinBox:":", the password is on the label of the antenna (Password); if there is no password there - empty. Update RouterOS: System → Packages → Check for updates. Optionally start clean: System → Reset Configuration → No Default Configuration (deletes all settings; afterwards you connect only by MAC). Then in the WinBox terminal:","Точка за достъп (A) - меню WiFi":"Access point (A) - WiFi menu","Клиент (Б) - меню WiFi":"Client (B) - WiFi menu","Ако антената е с другото меню (Wireless):":"If the antenna has the other menu (Wireless):","Точка за достъп (A) - меню Wireless":"Access point (A) - Wireless menu","Клиент (Б) - меню Wireless":"Client (B) - Wireless menu","Паролата на връзката (":"The link password (",") трябва да е":") must be","еднаква":"the same","на двете антени. Сменете я и сменете паролата на":"on both antennas. Change it and change the password of","Последните два реда (API и потребител само за четене) не са задължителни, но потребителят само за четене е по-безопасен за програмата.":"The last two lines (API and read-only user) are not mandatory, but the read-only user is safer for the program.","IP адресите са пример - сложете различни за двете антени в мрежата си.":"The IP addresses are an example - use different ones for the two antennas in your network.","Каналът е 20 MHz, докато насочвате - сигналът е по-стабилен и се отчита по-лесно. Разширявате го накрая (стъпка 9).":"The channel is 20 MHz while you aim - the signal is more stable and easier to read. You widen it at the end (step 9).","5500 MHz е DFS честота: след включване точката за достъп изчаква 1 минута (на 5600-5650 MHz - 10 минути), преди да излъчва. Това е нормално.":"5500 MHz is a DFS frequency: after power-on the access point waits 1 minute (on 5600-5650 MHz - 10 minutes) before transmitting. This is normal.","WiFi е новият пакет (ax модели, интерфейс":"WiFi is the new package (ax models, interface","), Wireless - старият (":"), Wireless - the old one (","). Програмата чете и двата.":"). The program reads both.","Проба: включете двете антени на бюрото, обърнати встрани една от друга. В WiFi → Registration на A трябва да се появи Б.":"Test: switch on both antennas on the desk, turned away from each other. In WiFi → Registration on A, B should appear.","4. Планиране (раздел „План и карта“)":"4. Planning (the “Plan and map” tab)","Поставете точките A и Б на сателитната карта точно върху сградите или мачтите. Координатите можете да вземете и от Google Maps: десен бутон върху мястото → копирайте числата.":"Place points A and B on the satellite map exactly on the buildings or masts. You can also take the coordinates from Google Maps: right-click on the place → copy the numbers.","„Антена над терена“ е височината от земята до антената, включително сградата. Например 4-етажен блок (~12 m) + 3 m мачта = 15 m.":"“Antenna above ground” is the height from the ground to the antenna, including the building. For example a 4-storey block (~12 m) + 3 m mast = 15 m.","Височината на терена се взима автоматично от модел на релефа (90 m точност). Ако я знаете по-точно, напишете я.":"The terrain height is taken automatically from a terrain model (90 m accuracy). If you know it more precisely, type it in.","Ако има гора или сгради по трасето, попълнете „Дървета/сгради“ (например 15 m). Моделът не вижда отделни дървета.":"If there is forest or buildings along the path, fill in “Trees/buildings” (for example 15 m). The model does not see individual trees.","Проверете „Видимост“. Трябва поне 60% от зоната на Френел да е свободна":"Check “Visibility”. At least 60% of the Fresnel zone must be clear","(за вас това е":"(for you that is","m около линията в средата)":"m around the line in the middle)",". Ако не е, вдигнете антените.":". If it is not, raise the antennas.","С „Изпрати плана на другия компютър“ имате еднакви данни от двете страни.":"With “Send the plan to the other computer” both sides have the same data.","5. Монтаж":"5. Mounting","Монтирайте на изчислената височина, на твърда мачта. Двете антени трябва да са завъртени еднакво около оста си (например и двете с кабела надолу). Иначе поляризацията не съвпада и сигналът пада.":"Mount at the calculated height, on a rigid mast. Both antennas must be rotated the same way around their axis (for example both with the cable down). Otherwise the polarization does not match and the signal drops.","Заземете мачтата. Направете водна примка на кабела (да провисва надолу преди антената), за да не влиза вода в конектора.":"Ground the mast. Make a drip loop in the cable (let it sag down before the antenna) so that water does not enter the connector.","Затегнете стойката само толкова, че антената да не пада, но още да се върти на ръка.":"Tighten the mount only enough that the antenna does not fall, but can still be turned by hand.","6. Грубо насочване":"6. Rough aiming","Компас:":"Compass:","застанете на 2-3 m от металната мачта и от колата. На A насочете към":"stand 2-3 m away from the metal mast and from the car. At A aim at",", на Б - към":", at B - at",". Магнитното отклонение вече е отчетено (WMM2025).":". The magnetic declination is already taken into account (WMM2025).","Ориентир:":"Landmark:","на сателитната карта намерете нещо по линията (сграда, кула, дърво) и прицелете антената към него":"on the satellite map find something along the line (building, tower, tree) and aim the antenna at it",". На":". At","km другата страна обикновено се вижда с бинокъл":"km the other side can usually be seen with binoculars","Наклон:":"Tilt:","на A -":"at A -",", на Б -":", at B -",". Ползвайте скалата на стойката или приложение за наклон.":". Use the scale on the mount or a tilt app.","Грешката трябва да е под половината от ширината на лъча (≈":"The error must be under half of the beam width (≈","°), иначе антените няма да се „видят“.":"°), otherwise the antennas will not “see” each other.","7. Компютрите и програмата":"7. The computers and the program","Кабел от порт":"Cable from port","на PoE адаптера към компютъра. IP адреси не се настройват - програмата сама открива антената (както Neighbors в WinBox). Ако види няколко антени, изберете тази, която е с кабел към този компютър.":"of the PoE adapter to the computer. IP addresses are not configured - the program finds the antenna by itself (like Neighbors in WinBox). If it sees several antennas, choose the one that is connected by cable to this computer.","Wi-Fi на компютъра - към hotspot-а на телефона (за картата и за синхронизацията между двата компютъра).":"Computer Wi-Fi - to the hotspot of the phone (for the map and for the synchronization between the two computers).","Пуснете MikrotikAlignFree. Ако Windows попита за защитната стена, натиснете „Разреши“. В „Настройки“ включете синхронизацията, изберете страна (A/Б) и":"Start MikrotikAlignFree. If Windows asks about the firewall, click “Allow”. In “Settings” enable the synchronization, choose a side (A/B) and","една и съща стая":"the same room","на двата компютъра (бутон „Нов код“, после въведете същия код на другия).":"on both computers (button “New code”, then enter the same code on the other one).","Ако антената иска парола (от етикета или ваша), програмата я иска веднъж най-отгоре в „Насочване“.":"If the antenna asks for a password (from the label or your own), the program asks for it once at the top of “Aiming”.","Ако антената е в друга мрежа и не може да се стигне директно, натиснете „Настрой лаптопа автоматично“ и после „Да“ в прозореца на Windows. Кабелната карта получава временен IP; „Върни на автоматичен IP“ го премахва.":"If the antenna is on another network and cannot be reached directly, click “Set up the laptop automatically” and then “Yes” in the Windows window. The wired adapter gets a temporary IP; “Back to automatic IP” removes it.","Горе трябва да светят:":"At the top these should light up:","Антена ✓":"Antenna ✓","Интернет ✓":"Internet ✓","8. Фино насочване (раздел „Насочване“)":"8. Fine aiming (the “Aiming” tab)","Насочва само един човек в даден момент. Той натиска":"Only one person aims at a time. They click","„Аз насочвам“":"“I am aiming”",", а отсреща излиза надпис да не пипат антената.":", and the other side shows a notice not to touch the antenna.","Говорете през програмата (панел „Разговор“): бързи бутони като „Спри - пикът е тук“, текст, а за глас задръжте 🎤 (или клавиша":"Talk through the program (the “Chat” panel): quick buttons like “Stop - the peak is here”, text, and for voice hold 🎤 (or the key","), говорете и пуснете. Така телефонът остава само за интернет.":"), speak and release. This way the phone is used only for the internet.","Започнете от клиента (Б). Точката за достъп стои неподвижно след грубото насочване.":"Start with the client (B). The access point stays still after the rough aiming.","Включете „Звук“ (клавиш":"Turn on “Sound” (key","): по-висок и по-чест тон означава по-силен сигнал.":"): a higher and more frequent tone means a stronger signal.","Хоризонтално:":"Horizontally:","въртете много бавно, около 1° на стъпка, и изчаквайте 2-3 секунди. Подминете максимума от двете страни и се върнете там, където „dB от пика“ е 0.":"turn very slowly, about 1° per step, and wait 2-3 seconds. Go past the maximum on both sides and return to where “dB from peak” is 0.","Вертикално:":"Vertically:","същото с наклона.":"the same with the tilt.","Още веднъж хоризонтално и вертикално. След това насочва другата страна (A), а накрая пак малка корекция на Б.":"Once more horizontally and vertically. Then the other side (A) aims, and finally a small correction on B again.","Цел: около":"Target: about","dBm при A":"dBm at A","dBm при Б":"dBm at B","(±3-5 dB). Под големите числа програмата показва разликата от очакваното.":"(±3-5 dB). Under the big numbers the program shows the difference from the expected.","Страничен лъч:":"Side lobe:","ако сте с 10-20 dB под очакваното и не можете да го подобрите, вероятно сте хванали страничен лъч. Завъртете по-широко":"if you are 10-20 dB below the expected and cannot improve it, you have probably caught a side lobe. Turn wider","и потърсете по-силен максимум.":"and look for a stronger maximum.","Нулирайте пика („Нулирай пик“, клавиш":"Reset the peak (“Reset peak”, key","), когато започва другата страна. „Запази точка“ (клавиш":") when the other side starts. “Save point” (key",") записва измерването за протокола.":") records the measurement for the report.","Таблица на сигнала: -55 и по-добре - отличен; до -65 - много добър; до -72 - добър; до -78 - приемлив; по-слаб - проверете насочването и препятствията.":"Signal table: -55 and better - excellent; to -65 - very good; to -72 - good; to -78 - acceptable; weaker - check the aiming and the obstacles.","9. Проверка и разширяване на канала":"9. Check and channel widening","При меню WiFi RouterOS не дава Ch0/Ch1 и CCQ. Гледайте сигнала и скоростите (tx/rx rate) - трябва да са стабилни и близки до очакваните.":"With the WiFi menu RouterOS does not give Ch0/Ch1 and CCQ. Watch the signal and the rates (tx/rx rate) - they must be stable and close to the expected.","Тест на скоростта: раздел „Настройка на антената“, стъпка 6 - първо TCP, после UDP (или в WinBox: Tools → Bandwidth Test).":"Speed test: the “Antenna setup” tab, step 6 - first TCP, then UDP (or in WinBox: Tools → Bandwidth Test).","Разширете канала на 40 или 80 MHz (на двете антени:":"Widen the channel to 40 or 80 MHz (on both antennas:","или":"or",") и проверете пак. По-широкият канал дава по-висока скорост, но сигналът пада с около 3 dB за всяко удвояване на ширината.":") and check again. A wider channel gives higher speed, but the signal drops by about 3 dB for every doubling of the width.","При вашия сигнал се очаква":"With your signal, expect","Mbps реално при 20 MHz).":"Mbps real throughput at 20 MHz).","10. Финал":"10. Finish","Затягайте болтовете постепенно и следете сигнала. При затягане антената често мърда с 1-2°.":"Tighten the bolts gradually and watch the signal. While tightening, the antenna often moves by 1-2°.","Защитете конекторите от вода, проверете водната примка и заземяването.":"Protect the connectors from water, check the drip loop and the grounding.","Запазете точка с крайния сигнал от двете страни и направете протокол („Отчет“). Полезно е да го сравните по-късно, ако връзката се влоши.":"Save a point with the final signal on both sides and make a report (“Report”). It is useful to compare it later if the link gets worse.","Сменете паролата на":"Change the password of",". Ограничете API услугата само до вашата мрежа:":". Limit the API service to your network only:",", или я изключете, ако не ви трябва.":", or turn it off if you do not need it.","11. Проблеми":"11. Problems","Проблем":"Problem","Какво да проверите":"What to check","Антените не се виждат":"The antennas do not see each other","Грубото насочване е над половината от лъча; различни SSID, честота или парола; на DFS честота точката за достъп още чака (1-10 мин).":"The rough aiming is off by more than half of the beam; different SSID, frequency or password; on a DFS frequency the access point is still waiting (1-10 min).","„Антена: няма връзка“ / време за изчакване":"“Antenna: no connection” / timeout","Грешен IP адрес, компютърът не е в същата мрежа, кабелът е в грешния порт на PoE адаптера (трябва LAN). Опитайте избор на антена от списъка, „Търси по IP адреси“ и „Настрой лаптопа автоматично“.":"Wrong IP address, the computer is not on the same network, the cable is in the wrong port of the PoE adapter (it must be LAN). Try selecting the antenna from the list, “Search by IP addresses” and “Set up the laptop automatically”.","„Отказана връзка“":"“Connection refused”","API услугата е изключена или портът е различен:":"The API service is disabled or the port is different:","Иска парола":"Asks for a password","Въведете потребител и парола най-отгоре в „Насочване“. Паролата е на етикета на антената; потребителят трябва да има права":"Enter the username and password at the top of “Aiming”. The password is on the label of the antenna; the user must have the rights","Сигналът е с 10+ dB под очакваното":"The signal is 10+ dB below the expected","Страничен лъч, препятствие (дървета), различна поляризация или RouterOS намалява мощността заради country=bulgaria.":"Side lobe, obstacle (trees), different polarization, or RouterOS reduces the power because of country=bulgaria.","Сигналът скача":"The signal jumps","Мачтата се люлее, клони в зоната на Френел или смущения на същия канал - сменете честотата.":"The mast sways, branches in the Fresnel zone or interference on the same channel - change the frequency.","Добър сигнал, ниска скорост":"Good signal, low speed","Пробвайте друг канал (смущения); проверете дали Ethernet портът е на 1 Gbps.":"Try another channel (interference); check whether the Ethernet port runs at 1 Gbps.","„Отсреща: чака...“":"“Far end: waiting...”","Различна стая на двата компютъра, едни и същи страни (A/A) или няма интернет от телефона.":"A different room on the two computers, the same sides (A/A) or no internet from the phone.","Няма звук / микрофон":"No sound / microphone","Браузърът пуска звук и микрофон след разрешение и първо щракване. Проверете силата на звука на компютъра.":"The browser allows sound and microphone after permission and a first click. Check the volume of the computer.","Картата или релефът не се зареждат":"The map or the terrain do not load","Нужен е интернет (OpenStreetMap, Open-Meteo). Без релеф видимостта не е надеждна.":"Internet is needed (OpenStreetMap, Open-Meteo). Without terrain the visibility is not reliable.","Тестът на скоростта: не прие потребителя":"Speed test: the user was not accepted","Другата антена не е настроена със същата парола - въведете паролата на admin от етикета ѝ в стъпка 6.":"The other antenna is not set up with the same password - enter the admin password from its label in step 6.","12. Безопасност":"12. Safety","Работата на височина изисква осигуряване и втори човек. Не работете при вятър, дъжд или буря.":"Working at height requires securing and a second person. Do not work in wind, rain or storm.","Заземете антената и ползвайте мълниезащита на кабела.":"Ground the antenna and use lightning protection on the cable.","Не стойте дълго пред излъчваща антена на близко разстояние. Спазвайте местните изисквания за мощност (EIRP) и DFS в 5 GHz.":"Do not stay long in front of a transmitting antenna at close range. Follow the local requirements for power (EIRP) and DFS in 5 GHz.","Програмата слуша само на този компютър (127.0.0.1). Ползвайте отделен потребител с минимални права на антената.":"The program listens only on this computer (127.0.0.1). Use a separate user with minimal rights on the antenna.","13. Речник":"13. Glossary","- мощност на сигнала;":"- signal power;","- качество на връзката в проценти (при пакет wireless);":"- link quality in percent (package wireless);","- модулация (по-висок = по-бърз, но по-чувствителен);":"- modulation (higher = faster, but more sensitive);","- сигнал по двете вериги на антената.":"- signal on the two chains of the antenna.","- елипсоид около линията на видимост; препоръчва се поне 60% от нея да е свободна.":"- an ellipsoid around the line of sight; at least 60% of it should be clear.","Магнитно отклонение":"Magnetic declination","- разликата между географския и магнитния север.":"- the difference between geographic and magnetic north.","- точка за достъп / клиент, който прави мост към кабелната мрежа.":"- access point / client that makes a bridge to the wired network.","Language / Език":"Language / Език"},"srv":{"стартиране...":"starting...","Невалиден адрес.":"Invalid address.","Невалидни параметри.":"Invalid parameters.","демо":"demo","Тестът вече върви.":"The test is already running.","MikrotikAlignFree %s - отворете %s (Ctrl+C за изход)":"MikrotikAlignFree %s - open %s (Ctrl+C to quit)","няма регистрационна таблица":"no registration table","връзката с брокера е прекъсната":"connection to the broker lost","връзката е прекъсната":"connection lost","брокерът отказа връзката":"the broker refused the connection","ДЕМО-":"DEMO-","грешка от RouterOS":"error from RouterOS","фатална грешка от RouterOS":"fatal error from RouterOS","Включете синхронизацията (Настройки) и изчакайте връзка с брокера.":"Enable synchronization (Settings) and wait for a connection to the broker.","Изберете вид на пакета (wifi или wireless).":"Choose the package type (wifi or wireless).","Изберете роля.":"Choose a role.","Невалидно име на интерфейс.":"Invalid interface name.","SSID: 1-32 знака.":"SSID: 1-32 characters.","Паролата: 8-63 знака, само латински букви, цифри и символи.":"Password: 8-63 characters, only Latin letters, digits and symbols.","Честотата е извън обхватите 2.4 и 5 GHz.":"The frequency is outside the 2.4 and 5 GHz bands.","Невалидна ширина на канала.":"Invalid channel width.","Невалиден обхват.":"Invalid band.","Държавата се въвежда на латиница (напр. Bulgaria).":"The country is entered in Latin letters (e.g. Bulgaria).","Име на антената: латиница, цифри, - _ . (до 32 знака).":"Antenna name: Latin letters, digits, - _ . (up to 32 characters).","Невалидно име на кабелния порт.":"Invalid name of the cable port.","Потребител: 3-32 знака (латински букви, цифри, _ . -).":"User: 3-32 characters (Latin letters, digits, _ . -).","Паролата: поне 8 знака, само латински букви, цифри и символи.":"Password: at least 8 characters, only Latin letters, digits and symbols.","Разрешен адрес: напр. 192.168.88.10 или 192.168.88.0/24.":"Allowed address: e.g. 192.168.88.10 or 192.168.88.0/24.","Празно съобщение.":"Empty message.","Записът е празен или твърде дълъг.":"The recording is empty or too long.","няма":"none","Невалиден план.":"Invalid plan.","Няма зададен адрес на антената.":"No antenna address is set.","# създава или обновява: ":"# creates or updates: ","IP адрес: напр. 192.168.88.2/24.":"IP address: e.g. 192.168.88.2/24.","Невалиден шлюз.":"Invalid gateway.","Непознат вид настройка.":"Unknown kind of setting.","Липсва потвърждение.":"Confirmation is missing.","В демо режим не се променя нищо.":"In demo mode nothing is changed.","В демо режим не се прави копие.":"In demo mode no backup is made.","Тази функция е само за Windows.":"This function is for Windows only.","Невалидно име на адаптер.":"Invalid adapter name.","ДЕМО":"DEMO","SXTsq 5 ax (демо)":"SXTsq 5 ax (demo)","7.16 (демо)":"7.16 (demo)","Невалиден порт.":"Invalid port.","Търсенето вече върви.":"The search is already running.","Невалидна честота.":"Invalid frequency.","Невалиден IP адрес, маска или шлюз.":"Invalid IP address, mask or gateway.","netsh върна код %s (отказан UAC или грешка).":"netsh returned code %s (UAC declined or an error).","# мост: ако %s вече е в мост - ползва се той, иначе: /interface/bridge/add name=bridge1\n/interface/bridge/port/add bridge=<мост> interface=%s   # и %s":"# bridge: if %s is already in a bridge - it is used, otherwise: /interface/bridge/add name=bridge1\n/interface/bridge/port/add bridge=<bridge> interface=%s   # and %s","Вътрешна грешка: %s":"Internal error: %s","ДЕМО-ЛИНК":"DEMO-LINK","# изчиства DHCP сървър/клиент и излишни адреси\n":"# removes the DHCP server/client and extra addresses\n","/ip/address/set address=%s interface=<мост>   # връзката може да прекъсне":"/ip/address/set address=%s interface=<bridge>   # the connection may drop","връзката прекъсна - това е нормално след смяна на адреса":"the connection dropped - this is normal after changing the address","# резервно копие: %s.backup":"# backup: %s.backup","🎤 гласово съобщение":"🎤 voice message","# резервно копие":"# backup","Копието не е направено, нищо не е променено: ":"The backup was not made, nothing was changed: "}}''')
PAGE_RE = re.compile(r"[^<>\"'`{}\\$\n]+")


def TR(s):
    """Превод на съобщение от сървъра (само когато езикът е английски)."""
    return I18N["srv"].get(s, s) if LANG[0] == "en" else s


def render_page():
    lang = LANG[0]
    s = PAGE
    if lang == "en":
        table = I18N["page"]

        def one(mt):
            r = mt.group(0)
            k = r.strip()
            v = table.get(k)
            if v is None:
                return r
            i = r.index(k)
            return r[:i] + v.replace("\u201c", "\ue000").replace("\u201d", "\ue001") + r[i + len(k):]

        s = PAGE_RE.sub(one, s)
        s = s.replace("\u201c", "\u201d").replace("\u201e", "\u201c")
        s = s.replace("\ue000", "\u201c").replace("\ue001", "\u201d")
    return s.replace("__VERSION__", APP_VERSION).replace("__LANG__", lang)


# --------------------------------------------------------------------------- RouterOS API
class RouterOSError(Exception):
    pass


def enc_len(n):
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return (n | 0x8000).to_bytes(2, "big")
    if n < 0x200000:
        return (n | 0xC00000).to_bytes(3, "big")
    if n < 0x10000000:
        return (n | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + n.to_bytes(4, "big")


class Api:
    def __init__(self, host, port=8728, use_ssl=False, timeout=4.0):
        sock = socket.create_connection((host, port), timeout)
        if use_ssl:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # RouterOS обикновено ползва самоподписан сертификат
            try:
                ctx.set_ciphers("ALL:@SECLEVEL=0")
            except ssl.SSLError:
                pass
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
        self.sock = sock

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _send_word(self, word):
        data = word.encode("utf-8")
        self.sock.sendall(enc_len(len(data)) + data)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError(TR("връзката е прекъсната"))
            buf += chunk
        return buf

    def _read_len(self):
        b = self._recv(1)[0]
        if b < 0x80:
            return b
        if b < 0xC0:
            return ((b & 0x3F) << 8) | self._recv(1)[0]
        if b < 0xE0:
            r = self._recv(2)
            return ((b & 0x1F) << 16) | (r[0] << 8) | r[1]
        if b < 0xF0:
            r = self._recv(3)
            return ((b & 0x0F) << 24) | int.from_bytes(r, "big")
        return int.from_bytes(self._recv(4), "big")

    def _read_sentence(self):
        words = []
        while True:
            n = self._read_len()
            if n == 0:
                return words
            words.append(self._recv(n).decode("utf-8", "replace"))

    def _send_command(self, command, attrs):
        for w in (command,) + tuple(attrs):
            self._send_word(w)
        self._send_word("")

    @staticmethod
    def _attrs(sentence):
        data = {}
        for w in sentence[1:]:
            if w.startswith("="):
                k, _, v = w[1:].partition("=")
                data[k] = v
        return data

    def talk(self, command, *attrs):
        self._send_command(command, attrs)
        rows = []
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            kind, data = sentence[0], self._attrs(sentence)
            if kind == "!re":
                rows.append(data)
            elif kind == "!done":
                return rows, data
            elif kind == "!trap":
                raise RouterOSError(data.get("message", TR("грешка от RouterOS")))
            elif kind == "!fatal":
                raise RouterOSError(TR("фатална грешка от RouterOS"))

    def stream(self, command, attrs, on_row, stop, max_seconds):
        """Команда с продължителен изход (например bandwidth-test)."""
        self.sock.settimeout(12)
        self._send_command(command, attrs)
        end = time.time() + max_seconds
        while not stop.is_set() and time.time() < end:
            sentence = self._read_sentence()
            if not sentence:
                continue
            kind, data = sentence[0], self._attrs(sentence)
            if kind == "!re":
                on_row(data)
            elif kind == "!done":
                return
            elif kind == "!trap":
                raise RouterOSError(data.get("message", TR("грешка от RouterOS")))
            elif kind == "!fatal":
                raise RouterOSError(TR("фатална грешка от RouterOS"))

    def login(self, user, password):
        _, done = self.talk("/login", "=name=" + user, "=password=" + password)
        if "ret" in done:  # стар метод с предизвикателство (преди RouterOS 6.43)
            challenge = bytes.fromhex(done["ret"])
            digest = hashlib.md5(b"\x00" + password.encode("utf-8") + challenge).hexdigest()
            self.talk("/login", "=name=" + user, "=response=00" + digest)


# --------------------------------------------------------------------------- общо състояние
ST = {"local": {"ok": False, "error": TR("стартиране..."), "ts": 0, "peers": []}, "far": {"enabled": False},
      "mqtt": {"enabled": False}, "btest": {"state": "idle"}}
LOCK = threading.Lock()
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def set_state(key, **kw):
    with LOCK:
        ST[key] = kw


def to_num(value):
    if value is None:
        return None
    m = NUM.search(str(value))
    return float(m.group(0)) if m else None


def parse_row(row):
    """Единен вид за двата пакета: wifi (RouterOS 7) и wireless (класически)."""
    return {
        "mac": row.get("mac-address", ""),
        "interface": row.get("interface", ""),
        "signal": to_num(row.get("signal", row.get("signal-strength"))),
        "tx_signal": to_num(row.get("tx-signal-strength")),
        "tx_rate": row.get("tx-rate", ""),
        "rx_rate": row.get("rx-rate", ""),
        "ccq": to_num(row.get("tx-ccq", row.get("ccq"))),
        "tx_ccq": to_num(row.get("tx-ccq", row.get("ccq"))),
        "rx_ccq": to_num(row.get("rx-ccq")),
        "ch0": to_num(row.get("signal-strength-ch0")),
        "ch1": to_num(row.get("signal-strength-ch1")),
        "snr": to_num(row.get("signal-to-noise")),
        "noise": to_num(row.get("noise-floor")),
        "distance": to_num(row.get("distance")),
        "uptime": row.get("uptime", ""),
    }


def poll_router(api, cfg, ctx):
    paths = ["/interface/wifi/registration-table/print", "/interface/wireless/registration-table/print"]
    if ctx.get("path"):
        paths = [ctx["path"]]
    rows, last_err = None, None
    for p in paths:
        try:
            rows, _ = api.talk(p)
            ctx["path"] = p
            break
        except RouterOSError as e:
            last_err = e
    if rows is None:
        raise last_err or RouterOSError(TR("няма регистрационна таблица"))
    want = cfg.get("interface", "").strip()
    if want:
        rows = [r for r in rows if r.get("interface") == want]
    return [parse_row(r) for r in rows]


def demo_values(t, shift=0.0, name="demo"):
    base = -68 + 9 * math.sin((t + shift) / 7.0)
    return [{
        "mac": "DE:MO:00:00:00:01", "interface": name, "signal": round(base + random.uniform(-1, 1)),
        "tx_signal": round(base - 1 + random.uniform(-1, 1)), "tx_rate": "866Mbps-80MHz/2S",
        "rx_rate": "780Mbps-80MHz/2S", "ccq": None, "tx_ccq": None, "rx_ccq": None,
        "ch0": round(base + random.uniform(-2, 1)), "ch1": round(base - 1 + random.uniform(-2, 1)),
        "snr": round(base + 92), "noise": -92, "distance": 4, "uptime": TR("демо"),
    }]


class RouterPoller(threading.Thread):
    """Опитва една антена. key='local' (router_*) или 'far' (router2_*)."""

    def __init__(self, key, prefix):
        super().__init__(daemon=True)
        self.key, self.prefix = key, prefix

    def run(self):
        api, ctx, identity, seen = None, {}, "", -1
        p = self.prefix
        while True:
            cfg = load_cfg()
            if seen != CFG_VERSION[0]:
                seen = CFG_VERSION[0]
                if api:
                    api.close()
                api, ctx, identity = None, {}, ""
            try:
                if self.key == "far" and not (cfg["router2_enabled"] and (cfg["router2_host"] or cfg["demo"])):
                    if api:
                        api.close()
                    api = None
                    set_state("far", enabled=False)
                    time.sleep(1)
                    continue
                if cfg["demo"]:
                    shift = 0.0 if self.key == "local" else -2.5
                    peers, identity = demo_values(time.time(), shift, self.key), TR("ДЕМО-") + self.key
                else:
                    if api is None:
                        api = Api(cfg[p + "_host"], int(cfg[p + "_port"]), bool(cfg[p + "_ssl"]))
                        api.login(cfg[p + "_user"], cfg[p + "_password"])
                        try:
                            rows, _ = api.talk("/system/identity/print")
                            identity = rows[0].get("name", "") if rows else ""
                        except RouterOSError:
                            identity = ""
                    peers = poll_router(api, cfg, ctx)
                    if time.time() - ctx.get("if_ts", 0) > 5:
                        ctx["if_ts"] = time.time()
                        try:
                            ctx["ifaces"] = read_ifaces(api)[1]
                        except (RouterOSError, ValueError):
                            pass
                if cfg["demo"]:
                    ctx["ifaces"] = [{"name": "wifi1", "mode": "ap" if self.key == "local" else "station-bridge",
                                      "frequency": "5500", "width": "20mhz", "running": "true", "disabled": "false"}]
                set_state(self.key, enabled=True, ok=True, error="", ts=time.time(), identity=identity,
                          peers=peers, path=ctx.get("path", ""), ifaces=ctx.get("ifaces", []))
            except (OSError, ConnectionError, RouterOSError, ValueError) as e:
                if api:
                    api.close()
                api, ctx = None, {}
                set_state(self.key, enabled=True, ok=False, error=str(e) or e.__class__.__name__, ts=time.time(),
                          peers=[], kind=classify_error(e))
                time.sleep(2)
            time.sleep(max(0.2, int(cfg.get("poll_ms", 500)) / 1000.0))


# --------------------------------------------------------------------------- тест на скоростта
BT_STOP = threading.Event()
BT_LOCK = threading.Lock()


def mbps(value):
    n = to_num(value)
    if n is None:
        return None
    s = str(value).lower()
    if "gbps" in s:
        return n * 1000
    if "mbps" in s:
        return n
    if "kbps" in s:
        return n / 1000.0
    return n / 1e6  # чисто число = bit/s


def btest_update(**kw):
    with LOCK:
        ST["btest"].update(kw)


def btest_worker(cfg, params):
    api = None
    keys = [("tx_now", "tx-current"), ("rx_now", "rx-current"), ("tx_avg", "tx-total-average"),
            ("rx_avg", "rx-total-average")]

    def on_row(row):
        upd = {name: mbps(row[k]) for name, k in keys if k in row}
        upd["status"] = row.get("status", "")
        if "lost-packets" in row:
            upd["lost"] = to_num(row["lost-packets"])
        btest_update(**upd)

    try:
        if cfg["demo"]:
            for i in range(params["duration"]):
                if BT_STOP.is_set():
                    break
                v = 480 + random.uniform(-40, 40)
                btest_update(tx_now=v, rx_now=v * 0.9, tx_avg=v, rx_avg=v * 0.9, status="running")
                time.sleep(1)
        else:
            api = Api(cfg["router_host"], int(cfg["router_port"]), bool(cfg["router_ssl"]))
            api.login(cfg["router_user"], cfg["router_password"])
            attrs = ["=address=" + params["address"], "=protocol=" + params["protocol"],
                     "=direction=" + params["direction"], "=duration=%ds" % params["duration"]]
            if params.get("password"):
                attrs += ["=user=" + (cfg["btest_user"] or "admin"), "=password=" + params["password"]]
            elif cfg["btest_user"]:
                attrs += ["=user=" + cfg["btest_user"], "=password=" + cfg["btest_password"]]
            api.stream("/tool/bandwidth-test", attrs, on_row, BT_STOP, params["duration"] + 20)
        btest_update(state="stopped" if BT_STOP.is_set() else "done")
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        btest_update(state="error", error=str(e) or e.__class__.__name__)
    finally:
        if api:
            api.close()


def btest_start(cfg, data):
    addr = str(data.get("address", "")).strip()
    proto = data.get("protocol", "tcp")
    direction = data.get("direction", "both")
    try:
        duration = int(data.get("duration", 10))
    except (TypeError, ValueError):
        duration = 10
    if not re.fullmatch(r"[A-Za-z0-9.:\-]{1,64}", addr):
        return TR("Невалиден адрес.")
    if proto not in ("tcp", "udp") or direction not in ("both", "send", "receive"):
        return TR("Невалидни параметри.")
    with BT_LOCK:
        if ST["btest"].get("state") == "running":
            return TR("Тестът вече върви.")
        BT_STOP.clear()
        with LOCK:
            ST["btest"] = {"state": "running", "address": addr, "protocol": proto, "direction": direction,
                           "duration": max(3, min(60, duration)), "started": time.time()}
        params = {"address": addr, "protocol": proto, "direction": direction, "duration": max(3, min(60, duration)),
                  "password": str(data.get("password", ""))[:64]}
        threading.Thread(target=btest_worker, args=(cfg, params), daemon=True).start()
    return ""


# --------------------------------------------------------------------------- магнитно отклонение (WMM2025)
# Коефициентите на модела WMM2025 (NOAA/BGS) са общодостъпни. Валиден от 2025.0 до 2030.0.
WMM_EPOCH = 2025.0
WMM_COF = """
1 0 -29351.8 0.0 12.0 0.0
1 1 -1410.8 4545.4 9.7 -21.5
2 0 -2556.6 0.0 -11.6 0.0
2 1 2951.1 -3133.6 -5.2 -27.7
2 2 1649.3 -815.1 -8.0 -12.1
3 0 1361.0 0.0 -1.3 0.0
3 1 -2404.1 -56.6 -4.2 4.0
3 2 1243.8 237.5 0.4 -0.3
3 3 453.6 -549.5 -15.6 -4.1
4 0 895.0 0.0 -1.6 0.0
4 1 799.5 278.6 -2.4 -1.1
4 2 55.7 -133.9 -6.0 4.1
4 3 -281.1 212.0 5.6 1.6
4 4 12.1 -375.6 -7.0 -4.4
5 0 -233.2 0.0 0.6 0.0
5 1 368.9 45.4 1.4 -0.5
5 2 187.2 220.2 0.0 2.2
5 3 -138.7 -122.9 0.6 0.4
5 4 -142.0 43.0 2.2 1.7
5 5 20.9 106.1 0.9 1.9
6 0 64.4 0.0 -0.2 0.0
6 1 63.8 -18.4 -0.4 0.3
6 2 76.9 16.8 0.9 -1.6
6 3 -115.7 48.8 1.2 -0.4
6 4 -40.9 -59.8 -0.9 0.9
6 5 14.9 10.9 0.3 0.7
6 6 -60.7 72.7 0.9 0.9
7 0 79.5 0.0 -0.0 0.0
7 1 -77.0 -48.9 -0.1 0.6
7 2 -8.8 -14.4 -0.1 0.5
7 3 59.3 -1.0 0.5 -0.8
7 4 15.8 23.4 -0.1 0.0
7 5 2.5 -7.4 -0.8 -1.0
7 6 -11.1 -25.1 -0.8 0.6
7 7 14.2 -2.3 0.8 -0.2
8 0 23.2 0.0 -0.1 0.0
8 1 10.8 7.1 0.2 -0.2
8 2 -17.5 -12.6 0.0 0.5
8 3 2.0 11.4 0.5 -0.4
8 4 -21.7 -9.7 -0.1 0.4
8 5 16.9 12.7 0.3 -0.5
8 6 15.0 0.7 0.2 -0.6
8 7 -16.8 -5.2 -0.0 0.3
8 8 0.9 3.9 0.2 0.2
9 0 4.6 0.0 -0.0 0.0
9 1 7.8 -24.8 -0.1 -0.3
9 2 3.0 12.2 0.1 0.3
9 3 -0.2 8.3 0.3 -0.3
9 4 -2.5 -3.3 -0.3 0.3
9 5 -13.1 -5.2 0.0 0.2
9 6 2.4 7.2 0.3 -0.1
9 7 8.6 -0.6 -0.1 -0.2
9 8 -8.7 0.8 0.1 0.4
9 9 -12.9 10.0 -0.1 0.1
10 0 -1.3 0.0 0.1 0.0
10 1 -6.4 3.3 0.0 0.0
10 2 0.2 0.0 0.1 -0.0
10 3 2.0 2.4 0.1 -0.2
10 4 -1.0 5.3 -0.0 0.1
10 5 -0.6 -9.1 -0.3 -0.1
10 6 -0.9 0.4 0.0 0.1
10 7 1.5 -4.2 -0.1 0.0
10 8 0.9 -3.8 -0.1 -0.1
10 9 -2.7 0.9 -0.0 0.2
10 10 -3.9 -9.1 -0.0 -0.0
11 0 2.9 0.0 0.0 0.0
11 1 -1.5 0.0 -0.0 -0.0
11 2 -2.5 2.9 0.0 0.1
11 3 2.4 -0.6 0.0 -0.0
11 4 -0.6 0.2 0.0 0.1
11 5 -0.1 0.5 -0.1 -0.0
11 6 -0.6 -0.3 0.0 -0.0
11 7 -0.1 -1.2 -0.0 0.1
11 8 1.1 -1.7 -0.1 -0.0
11 9 -1.0 -2.9 -0.1 0.0
11 10 -0.2 -1.8 -0.1 0.0
11 11 2.6 -2.3 -0.1 0.0
12 0 -2.0 0.0 0.0 0.0
12 1 -0.2 -1.3 0.0 -0.0
12 2 0.3 0.7 -0.0 0.0
12 3 1.2 1.0 -0.0 -0.1
12 4 -1.3 -1.4 -0.0 0.1
12 5 0.6 -0.0 -0.0 -0.0
12 6 0.6 0.6 0.1 -0.0
12 7 0.5 -0.1 -0.0 -0.0
12 8 -0.1 0.8 0.0 0.0
12 9 -0.4 0.1 0.0 -0.0
12 10 -0.2 -1.0 -0.1 -0.0
12 11 -1.3 0.1 -0.0 0.0
12 12 -0.7 0.2 -0.1 -0.1
"""
_WMM = {}


def _wmm_coeffs():
    if not _WMM:
        for line in WMM_COF.strip().splitlines():
            n, m, g, h, gd, hd = line.split()
            _WMM[(int(n), int(m))] = (float(g), float(h), float(gd), float(hd))
    return _WMM


def decimal_year(now=None):
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return now.year + (now - start).total_seconds() / (end - start).total_seconds()


def wmm_declination(lat, lon, alt_km=0.0, year=None):
    """Магнитно отклонение в градуси (изток = +)."""
    year = decimal_year() if year is None else year
    coef = _wmm_coeffs()
    lat = max(-89.99, min(89.99, lat))
    a, f, a_ref, nmax = 6378.137, 1 / 298.257223563, 6371.2, 12
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    sp, cp = math.sin(phi), math.cos(phi)
    rc = a / math.sqrt(1 - e2 * sp * sp)
    p = (rc + alt_km) * cp
    z = (rc * (1 - e2) + alt_km) * sp
    r = math.hypot(p, z)
    phic = math.asin(z / r)
    x, s = math.sin(phic), math.cos(phic)  # cos и sin на допълнението до геоцентричната ширина
    P = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    dP = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    P[0][0] = 1.0
    for n in range(1, nmax + 1):
        for m in range(0, n + 1):
            if m == n:
                k = math.sqrt((2 * n - 1) / (2.0 * n)) if n > 1 else 1.0
                P[n][n] = k * s * P[n - 1][n - 1]
                dP[n][n] = k * (s * dP[n - 1][n - 1] + x * P[n - 1][n - 1])
            else:
                d = math.sqrt(n * n - m * m)
                c = math.sqrt((n - 1) ** 2 - m * m)
                p2 = P[n - 2][m] if n >= 2 else 0.0
                dp2 = dP[n - 2][m] if n >= 2 else 0.0
                P[n][m] = ((2 * n - 1) * x * P[n - 1][m] - c * p2) / d
                dP[n][m] = ((2 * n - 1) * (x * dP[n - 1][m] - s * P[n - 1][m]) - c * dp2) / d
    dt = year - WMM_EPOCH
    ar = a_ref / r
    br = bt = bp = 0.0
    for n in range(1, nmax + 1):
        arn = ar ** (n + 2)
        for m in range(0, n + 1):
            g, h, gd, hd = coef[(n, m)]
            g, h = g + dt * gd, h + dt * hd
            cm, sm = math.cos(m * lam), math.sin(m * lam)
            t1 = g * cm + h * sm
            br += (n + 1) * arn * t1 * P[n][m]
            bt -= arn * t1 * dP[n][m]
            bp += arn * m * (g * sm - h * cm) * P[n][m]
    bp /= s
    psi = phi - phic
    x_n = -bt * math.cos(psi) - br * math.sin(psi)
    return math.degrees(math.atan2(bp, x_n))


# --------------------------------------------------------------------------- MQTT синхронизация
def mq_str(text):
    b = text.encode("utf-8")
    return len(b).to_bytes(2, "big") + b


def mq_packet(first, payload):
    n, rem = len(payload), b""
    while True:
        d, n = n % 128, n // 128
        rem += bytes([d | (128 if n else 0)])
        if not n:
            break
    return bytes([first]) + rem + payload


def parse_brokers(text):
    out = []
    for item in re.split(r"[\s,;]+", text or ""):
        if not item:
            continue
        m = re.fullmatch(r"(?:(mqtts?)://)?([A-Za-z0-9.\-]+)(?::(\d{1,5}))?", item)
        if not m:
            continue
        scheme = m.group(1) or "mqtts"
        out.append((scheme == "mqtts", m.group(2), int(m.group(3) or (8883 if scheme == "mqtts" else 1883))))
    return out


class MqttSync(threading.Thread):
    """Минимален MQTT 3.1.1 клиент (QoS 0). Публикува моя сигнал и приема сигнала на другата страна."""

    def __init__(self):
        super().__init__(daemon=True)

    def run(self):
        idx = 0
        while True:
            cfg = load_cfg()
            room = clean_room(cfg["room"])
            brokers = parse_brokers(cfg["mqtt_brokers"])
            if not cfg["mqtt_enabled"] or not room or not brokers:
                set_state("mqtt", enabled=False)
                time.sleep(1)
                continue
            tls, host, port = brokers[idx % len(brokers)]
            try:
                self.session(cfg, tls, host, port, room)
            except (OSError, ConnectionError, ValueError, ssl.SSLError) as e:
                set_state("mqtt", enabled=True, ok=False, error=str(e) or e.__class__.__name__, broker=host)
                idx += 1
                time.sleep(3)

    @staticmethod
    def read_packet(sock):
        sock.settimeout(0.5)
        try:
            first = sock.recv(1)
        except socket.timeout:
            return None
        if not first:
            raise ConnectionError(TR("връзката с брокера е прекъсната"))
        sock.settimeout(8)

        def need(n):
            buf = b""
            while len(buf) < n:
                c = sock.recv(n - len(buf))
                if not c:
                    raise ConnectionError(TR("връзката с брокера е прекъсната"))
                buf += c
            return buf

        mult, rem = 1, 0
        while True:
            d = need(1)[0]
            rem += (d & 127) * mult
            if not d & 128:
                break
            mult *= 128
        return first[0], (need(rem) if rem else b"")

    def session(self, cfg, tls, host, port, room):
        side = cfg["side"] if cfg["side"] in ("a", "b") else "a"
        topic_base = "alignfree/v1/%s/" % room
        sig = (tls, host, port, room, side)
        sock = socket.create_connection((host, port), 8)
        if tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        try:
            cid = "af-" + secrets.token_hex(6)
            sock.sendall(mq_packet(0x10, mq_str("MQTT") + b"\x04\x02" + (30).to_bytes(2, "big") + mq_str(cid)))
            pkt = self.read_packet(sock)
            while pkt is None:
                pkt = self.read_packet(sock)
            if pkt[0] != 0x20 or len(pkt[1]) < 2 or pkt[1][1] != 0:
                raise ConnectionError(TR("брокерът отказа връзката"))
            sock.sendall(mq_packet(0x82, (1).to_bytes(2, "big") + mq_str(topic_base + "+") + b"\x00"))
            sock.sendall(mq_packet(0x82, (2).to_bytes(2, "big") + mq_str(topic_base + "+/+") + b"\x00"))
            set_state("mqtt", enabled=True, ok=True, error="", broker=host, side=side, remote=None)
            last_ping = last_pub = last_chk = time.time()
            remote = None
            while True:
                now = time.time()
                if now - last_chk > 2:
                    last_chk = now
                    c2 = load_cfg()
                    s2 = c2["side"] if c2["side"] in ("a", "b") else "a"
                    b2 = parse_brokers(c2["mqtt_brokers"])
                    if not c2["mqtt_enabled"] or clean_room(c2["room"]) != room or s2 != side or \
                            (tls, host, port) not in b2:
                        return
                if now - last_pub >= 0.5:
                    last_pub = now
                    with LOCK:
                        loc = dict(ST["local"])
                    peer = (loc.get("peers") or [None])[0]
                    if loc.get("ok") and peer:
                        ifc = (loc.get("ifaces") or [{}])[0]
                        msg = json.dumps({"v": 1, "sig": peer["signal"], "tx": peer["tx_signal"],
                                          "txr": peer["tx_rate"], "rxr": peer["rx_rate"],
                                          "id": loc.get("identity", ""),
                                          "x": {"ch0": peer.get("ch0"), "ch1": peer.get("ch1"),
                                                "tc": peer.get("tx_ccq"), "rc": peer.get("rx_ccq"),
                                                "snr": peer.get("snr"), "nf": peer.get("noise"),
                                                "up": peer.get("uptime"), "mode": ifc.get("mode", ""),
                                                "fr": ifc.get("frequency", ""), "w": ifc.get("width", "")}})
                        sock.sendall(mq_packet(0x30, mq_str(topic_base + side) + msg.encode("utf-8")))
                with CHAT_LOCK:
                    outgoing, CHAT_OUT[:] = list(CHAT_OUT), []
                for sub, payload in outgoing:
                    sock.sendall(mq_packet(0x30, mq_str(topic_base + sub + "/" + side) +
                                           json.dumps(payload).encode("utf-8")))
                if now - last_ping > 15:
                    last_ping = now
                    sock.sendall(b"\xc0\x00")
                pkt = self.read_packet(sock)
                if pkt and (pkt[0] >> 4) == 3:
                    n = int.from_bytes(pkt[1][:2], "big")
                    topic = pkt[1][2:2 + n].decode("utf-8", "replace")
                    body = pkt[1][2 + n:]
                    if (pkt[0] >> 1) & 3:
                        body = body[2:]
                    rest = topic[len(topic_base):] if topic.startswith(topic_base) else ""
                    if "/" in rest:
                        if rest.rpartition("/")[2] != side:
                            sync_in(rest, body)
                    elif topic.startswith(topic_base) and rest != side:
                        try:
                            d = json.loads(body.decode("utf-8"))
                            remote = {"signal": to_num(d.get("sig")), "tx_signal": to_num(d.get("tx")),
                                      "tx_rate": str(d.get("txr", "")), "rx_rate": str(d.get("rxr", "")),
                                      "identity": str(d.get("id", ""))[:40], "ts": time.time(),
                                      "x": d.get("x") if isinstance(d.get("x"), dict) else {},
                                      "side": topic[len(topic_base):]}
                        except ValueError:
                            pass
                        set_state("mqtt", enabled=True, ok=True, error="", broker=host, side=side, remote=remote)
        finally:
            try:
                sock.sendall(b"\xe0\x00")
            except OSError:
                pass
            sock.close()


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MikrotikAlignFree/" + APP_VERSION

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(min(length, 65536)).decode("utf-8") or "{}")
        except (ValueError, OSError):
            return None

    def _local_origin_ok(self):
        # защита срещу заявки от чужди сайтове към локалния сървър
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return False
        return origin is None or origin.split("//", 1)[-1] == host

    def do_GET(self):
        if not self._local_origin_ok():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        if url.path.startswith("/static/"):
            asset = static_asset(url.path[len("/static/"):])
            if not asset:
                return self._json({"error": "not found"}, 404)
            body, ctype = asset
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path in EXTRA_GET:
            self._json(EXTRA_GET[url.path](self, parse_qs(url.query)))
        elif url.path in ("/", "/index.html"):
            body = render_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/state":
            with LOCK:
                out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ST.items()}
            out["now"] = time.time()
            self._json(out)
        elif url.path == "/api/config":
            cfg = load_cfg()
            for k in SECRET_KEYS:
                cfg["has_" + k] = bool(cfg.pop(k, ""))
            self._json(cfg)
        elif url.path == "/api/declination":
            q = parse_qs(url.query)
            try:
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
            except (KeyError, ValueError, IndexError):
                return self._json({"error": "bad request"}, 400)
            year = decimal_year()
            self._json({"declination": round(wmm_declination(lat, lon, 0.3, year), 2), "model": "WMM2025",
                        "year": round(year, 2), "valid": WMM_EPOCH <= year < WMM_EPOCH + 5})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local_origin_ok():
            return self._json({"error": "forbidden"}, 403)
        data = self._body()
        if data is None:
            return self._json({"error": "bad request"}, 400)
        if self.path == "/api/config":
            cfg = load_cfg()
            for key, default in DEFAULT_CFG.items():
                if key not in data:
                    continue
                if key in SECRET_KEYS:
                    if data[key]:
                        cfg[key] = str(data[key])
                elif key == "room":
                    cfg[key] = clean_room(data[key]) or cfg.get(key, "")
                elif key == "lang":
                    cfg[key] = "bg" if data[key] == "bg" else "en"
                elif key == "side":
                    cfg[key] = "b" if data[key] == "b" else "a"
                else:
                    try:
                        cfg[key] = type(default)(data[key])
                    except (TypeError, ValueError):
                        pass
            save_cfg(cfg)
            LANG[0] = cfg.get("lang", "en")
            CFG_VERSION[0] += 1
            self._json({"ok": True})
        elif self.path == "/api/btest":
            err = btest_start(load_cfg(), data)
            self._json({"ok": not err, "error": err})
        elif self.path == "/api/btest/stop":
            BT_STOP.set()
            self._json({"ok": True})
        elif self.path in EXTRA_POST:
            self._json(EXTRA_POST[self.path](self, data))
        elif self.path == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
        else:
            self._json({"error": "not found"}, 404)


# --------------------------------------------------------------------------- разширения (v0.3)
import base64
import concurrent.futures
import errno as _errno
import gzip
import subprocess

EXTRA_GET = {}
EXTRA_POST = {}
CHAT_OUT = []
CHAT_LOCK = threading.Lock()


def _guard(fn):
    def wrapper(handler, arg):
        try:
            return fn(handler, arg)
        except Exception as e:  # непредвидена грешка - връщаме разбираем отговор, а не 500
            return {"ok": False, "error": TR("Вътрешна грешка: %s") % (e,)}
    return wrapper


def route_get(path):
    def deco(fn):
        EXTRA_GET[path] = _guard(fn)
        return fn
    return deco


def route_post(path):
    def deco(fn):
        EXTRA_POST[path] = _guard(fn)
        return fn
    return deco


def classify_error(exc):
    """Кратък вид на грешката, за да покаже интерфейсът разбираемо обяснение."""
    if isinstance(exc, RouterOSError):
        text = str(exc).lower()
        return "auth" if ("invalid user" in text or "cannot log in" in text or "password" in text) else "ros"
    if isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, ssl.SSLError):
        return "ssl"
    if isinstance(exc, OSError):
        code = getattr(exc, "errno", None)
        winerr = getattr(exc, "winerror", None)
        if winerr == 10061 or code in (_errno.ECONNREFUSED,):
            return "refused"
        if winerr == 10060 or code in (_errno.ETIMEDOUT,):
            return "timeout"
        if winerr in (10051, 10065) or code in (_errno.ENETUNREACH, _errno.EHOSTUNREACH):
            return "unreachable"
        if isinstance(exc, socket.gaierror):
            return "dns"
    if isinstance(exc, ConnectionError):
        return "closed"
    return "other"


# --------------------------------------------------------------------------- синхронизация между двата компютъра
# CHAT_OUT е опашка от (подтема, речник) - изпраща се от MQTT нишката като <стая>/<подтема>/<страна>.
SYNC_IN = {"plan": None, "voice": {}}


def sync_queue(sub, payload):
    with CHAT_LOCK:
        CHAT_OUT.append((sub, payload))


def chat_add(side, text, mine=False, mid=""):
    text = str(text).strip()[:200]
    if not text:
        return
    with LOCK:
        chat = ST.setdefault("chat", {"seq": 0, "log": []})
        if mid and any(m.get("mid") == mid and m.get("side") == side for m in chat["log"]):
            return
        chat["seq"] += 1
        chat["log"] = (chat["log"] + [{"id": chat["seq"], "mid": mid, "side": side, "text": text, "ts": time.time(),
                                       "mine": mine, "delivered": False}])[-40:]


def sync_in(rest, body):
    kind, _, side = rest.partition("/")
    try:
        d = json.loads(bytes(body).decode("utf-8"))
        if not isinstance(d, dict):
            return
    except ValueError:
        return
    if kind == "chat":
        mid = str(d.get("id", ""))[:16]
        chat_add(side, d.get("text", ""), mid=mid)
        if mid:
            sync_queue("ack", {"id": mid})
    elif kind == "ack":
        mid = str(d.get("id", ""))[:16]
        with LOCK:
            for m in ST.get("chat", {}).get("log", []):
                if m.get("mine") and m.get("mid") == mid:
                    m["delivered"] = True
    elif kind == "aim":
        with LOCK:
            ST["aim_remote"] = {"on": bool(d.get("on")), "side": side, "ts": time.time()}
    elif kind == "plan":
        inputs = d.get("inputs")
        if isinstance(inputs, list) and len(inputs) < 60 and len(json.dumps(inputs)) < 20000:
            SYNC_IN["plan"] = inputs
            with LOCK:
                ST["plan_in"] = {"side": side, "ts": time.time()}
    elif kind == "voice":
        data = str(d.get("d", ""))
        if 0 < len(data) < 160000:
            vid = secrets.token_hex(4)
            SYNC_IN["voice"] = {"id": vid, "m": str(d.get("m", "audio/webm"))[:40], "d": data}
            chat_add(side, TR("🎤 гласово съобщение"), mid="v" + vid)
            with LOCK:
                ST["voice_in"] = {"id": vid, "side": side, "ts": time.time()}


def need_sync(cfg):
    with LOCK:
        mq = dict(ST.get("mqtt", {}))
    if not (cfg["mqtt_enabled"] and mq.get("ok")):
        return TR("Включете синхронизацията (Настройки) и изчакайте връзка с брокера.")
    return ""


@route_post("/api/chat")
def api_chat(handler, data):
    text = str(data.get("text", "")).strip()[:200]
    cfg = load_cfg()
    if not text:
        return {"ok": False, "error": TR("Празно съобщение.")}
    err = need_sync(cfg)
    if err:
        return {"ok": False, "error": err}
    mid = secrets.token_hex(4)
    sync_queue("chat", {"v": 1, "id": mid, "text": text})
    chat_add(cfg["side"], text, mine=True, mid=mid)
    return {"ok": True}


@route_post("/api/aim")
def api_aim(handler, data):
    on = bool(data.get("on"))
    cfg = load_cfg()
    if cfg["mqtt_enabled"] and not need_sync(cfg):
        sync_queue("aim", {"on": on})
    return {"ok": True}


@route_post("/api/voice")
def api_voice_send(handler, data):
    cfg = load_cfg()
    err = need_sync(cfg)
    if err:
        return {"ok": False, "error": err}
    d = str(data.get("d", ""))
    if not (0 < len(d) < 160000):
        return {"ok": False, "error": TR("Записът е празен или твърде дълъг.")}
    sync_queue("voice", {"m": str(data.get("m", "audio/webm"))[:40], "d": d})
    return {"ok": True}


@route_get("/api/voice")
def api_voice_get(handler, query):
    v = SYNC_IN.get("voice") or {}
    if query.get("id", [""])[0] != v.get("id"):
        return {"ok": False, "error": TR("няма")}
    return {"ok": True, "m": v["m"], "d": v["d"]}


@route_post("/api/plan/send")
def api_plan_send(handler, data):
    cfg = load_cfg()
    err = need_sync(cfg)
    if err:
        return {"ok": False, "error": err}
    inputs = data.get("inputs")
    if not isinstance(inputs, list) or len(json.dumps(inputs)) > 20000:
        return {"ok": False, "error": TR("Невалиден план.")}
    sync_queue("plan", {"v": 1, "inputs": inputs})
    return {"ok": True}


@route_get("/api/plan_in")
def api_plan_in(handler, query):
    return {"ok": SYNC_IN["plan"] is not None, "inputs": SYNC_IN["plan"]}


# --------------------------------------------------------------------------- откриване на антени (MNDP, UDP 5678)
NEIGH = {}


def mndp_parse(data):
    if len(data) < 8:
        return None
    pos, d = 4, {}
    while pos + 4 <= len(data):
        t = int.from_bytes(data[pos:pos + 2], "big")
        n = int.from_bytes(data[pos + 2:pos + 4], "big")
        v = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if t == 1 and len(v) == 6:
            d["mac"] = ":".join("%02X" % x for x in v)
        elif t == 5:
            d["identity"] = v.decode("utf-8", "replace")
        elif t == 7:
            d["version"] = v.decode("utf-8", "replace")
        elif t == 8:
            d["platform"] = v.decode("utf-8", "replace")
        elif t == 12:
            d["board"] = v.decode("utf-8", "replace")
        elif t == 16:
            d["iface"] = v.decode("utf-8", "replace")
        elif t == 17 and len(v) == 4:
            d["ip"] = socket.inet_ntoa(v)
    return d if d.get("mac") else None


def publish_neighbors():
    mine = local_ipv4s()
    nets = {ip.rsplit(".", 1)[0] for ip in mine}
    now = time.time()
    out = []
    for mac, d in sorted(NEIGH.items()):
        if now - d["seen"] > 180:
            continue
        ip = d.get("ip", "")
        out.append({"mac": mac, "identity": d.get("identity", ""), "board": d.get("board", ""),
                    "version": d.get("version", ""), "ip": ip, "age": int(now - d["seen"]),
                    "same_net": bool(ip) and ip.rsplit(".", 1)[0] in nets})
    with LOCK:
        ST["neighbors"] = out


def mndp_worker(port=5678):
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", port))
        sock.settimeout(1.0)
    except OSError:
        return
    last_req = 0.0
    while True:
        if time.time() - last_req > 30:
            last_req = time.time()
            try:
                sock.sendto(b"\x00\x00\x00\x00", ("255.255.255.255", port))
            except OSError:
                pass
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            publish_neighbors()
            continue
        except OSError:
            time.sleep(1)
            continue
        d = mndp_parse(data)
        if d:
            d.setdefault("ip", addr[0])
            d["seen"] = time.time()
            NEIGH[d["mac"]] = d
            publish_neighbors()


# --------------------------------------------------------------------------- свързване и четене от антена
def open_api(cfg, prefix="router", override=None):
    p = dict(host=cfg[prefix + "_host"], port=cfg[prefix + "_port"], ssl=cfg[prefix + "_ssl"],
             user=cfg[prefix + "_user"], password=cfg[prefix + "_password"])
    if override:
        p.update({k: v for k, v in override.items() if v not in (None, "")})
    if not p["host"]:
        raise RouterOSError(TR("Няма зададен адрес на антената."))
    api = Api(str(p["host"]), int(p["port"]), bool(p["ssl"]), timeout=5.0)
    try:
        api.login(str(p["user"]), str(p["password"]))
    except Exception:
        api.close()
        raise
    return api


def safe_talk(api, path, *attrs):
    try:
        return api.talk(path, *attrs)[0]
    except RouterOSError:
        return []


def read_ifaces(api):
    wifi = safe_talk(api, "/interface/wifi/print")
    if wifi:
        return "wifi", [{
            "name": r.get("name", ""), "mode": r.get("configuration.mode", ""), "ssid": r.get("configuration.ssid", ""),
            "frequency": r.get("channel.frequency", r.get("configuration.channel.frequency", "")),
            "width": r.get("channel.width", ""), "band": r.get("channel.band", ""),
            "disabled": r.get("disabled", ""), "running": r.get("running", "")} for r in wifi]
    wl = safe_talk(api, "/interface/wireless/print")
    return "wireless", [{
        "name": r.get("name", ""), "mode": r.get("mode", ""), "ssid": r.get("ssid", ""),
        "frequency": r.get("frequency", ""), "width": r.get("channel-width", ""), "band": r.get("band", ""),
        "disabled": r.get("disabled", ""), "running": r.get("running", "")} for r in wl]


def read_info(api):
    info = {"identity": "", "board": "", "version": "", "uptime": "", "cpu": "", "model": "", "firmware": "",
            "pkg": "", "interfaces": [], "addresses": []}
    rows = safe_talk(api, "/system/identity/print")
    info["identity"] = rows[0].get("name", "") if rows else ""
    rows = safe_talk(api, "/system/resource/print")
    if rows:
        r = rows[0]
        info.update(board=r.get("board-name", ""), version=r.get("version", ""), uptime=r.get("uptime", ""),
                    cpu=r.get("cpu-load", ""))
    rows = safe_talk(api, "/system/routerboard/print")
    if rows:
        info["model"] = rows[0].get("model", "")
        info["firmware"] = rows[0].get("current-firmware", "")
    wifi = safe_talk(api, "/interface/wifi/print")
    if wifi:
        info["pkg"] = "wifi"
        for r in wifi:
            info["interfaces"].append({
                "name": r.get("name", ""), "mode": r.get("configuration.mode", ""),
                "ssid": r.get("configuration.ssid", ""), "frequency": r.get("channel.frequency", ""),
                "width": r.get("channel.width", ""), "band": r.get("channel.band", ""),
                "country": r.get("configuration.country", ""), "disabled": r.get("disabled", ""),
                "running": r.get("running", ""), "configuration": r.get("configuration", "")})
    else:
        wl = safe_talk(api, "/interface/wireless/print")
        if wl:
            info["pkg"] = "wireless"
            for r in wl:
                info["interfaces"].append({
                    "name": r.get("name", ""), "mode": r.get("mode", ""), "ssid": r.get("ssid", ""),
                    "frequency": r.get("frequency", ""), "width": r.get("channel-width", ""),
                    "band": r.get("band", ""), "country": r.get("country", ""), "disabled": r.get("disabled", ""),
                    "running": r.get("running", ""), "configuration": r.get("security-profile", "")})
    for r in safe_talk(api, "/ip/address/print"):
        info["addresses"].append({"address": r.get("address", ""), "interface": r.get("interface", ""),
                                  "disabled": r.get("disabled", "")})
    return info


@route_get("/api/router_info")
def api_router_info(handler, query):
    which = (query.get("target", ["local"])[0])
    prefix = "router2" if which == "far" else "router"
    cfg = load_cfg()
    if cfg["demo"]:
        return {"ok": True, "info": {
            "identity": TR("ДЕМО"), "board": TR("SXTsq 5 ax (демо)"), "version": TR("7.16 (демо)"), "uptime": "3d4h", "cpu": "4",
            "model": "RBSXTsq5Pax", "firmware": "7.16", "pkg": "wifi",
            "interfaces": [{"name": "wifi1", "mode": "ap", "ssid": TR("ДЕМО-ЛИНК"), "frequency": "5500", "width": "20mhz",
                            "band": "5ghz-ax", "country": "Bulgaria", "disabled": "false", "running": "true",
                            "configuration": "cfg1"}],
            "addresses": [{"address": "192.168.88.1/24", "interface": "bridge", "disabled": "false"}]}}
    api = None
    try:
        api = open_api(cfg, prefix)
        return {"ok": True, "info": read_info(api)}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


@route_post("/api/test_router")
def api_test_router(handler, data):
    which = "router2" if data.get("which") == "router2" else "router"
    cfg = load_cfg()
    try:
        override = {"host": str(data.get("host", "")).strip(), "port": int(data.get("port") or 0) or None,
                    "user": str(data.get("user", "")), "password": str(data.get("password", "")),
                    "ssl": bool(data.get("ssl"))}
    except (TypeError, ValueError):
        return {"ok": False, "error": TR("Невалиден порт."), "kind": "other"}
    api = None
    try:
        api = open_api(cfg, which, override)
        rows = safe_talk(api, "/system/identity/print")
        res = safe_talk(api, "/system/resource/print")
        return {"ok": True, "identity": rows[0].get("name", "") if rows else "",
                "board": res[0].get("board-name", "") if res else "",
                "version": res[0].get("version", "") if res else ""}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


# --------------------------------------------------------------------------- търсене на антени в мрежата
def local_ipv4s():
    ips = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("203.0.113.1", 9))  # без изпращане на данни - само определя изходния адрес
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if ip.startswith(("10.", "192.168.", "172.")) and not ip.startswith("127."))


def probe_port(ip, port, timeout=0.5):
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


def scan_worker(port):
    mine = local_ipv4s()
    nets = sorted({ip.rsplit(".", 1)[0] for ip in mine})[:3]
    extra = ["192.168.88.1", "192.168.1.1", "192.168.0.1", "10.0.0.1", "192.168.88.2", "10.5.50.1"]
    targets = []
    for n in nets:
        targets += ["%s.%d" % (n, i) for i in range(1, 255)]
    for e in extra:
        if e not in targets:
            targets.append(e)
    targets = [t for t in targets if t not in mine]
    found, done = [], [0]
    with LOCK:
        ST["scan"] = {"state": "running", "found": [], "done": 0, "total": len(targets), "nets": nets, "mine": mine}

    def check(ip):
        ok = probe_port(ip, port)
        done[0] += 1
        if ok:
            found.append(ip)
        if done[0] % 10 == 0 or ok:
            with LOCK:
                ST["scan"].update(done=done[0], found=sorted(found))

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(check, targets))
    with LOCK:
        ST["scan"].update(state="done", done=len(targets), found=sorted(found))


@route_post("/api/scan")
def api_scan(handler, data):
    with LOCK:
        if ST.get("scan", {}).get("state") == "running":
            return {"ok": False, "error": TR("Търсенето вече върви.")}
    try:
        port = int(data.get("port") or 8728)
    except (TypeError, ValueError):
        port = 8728
    threading.Thread(target=scan_worker, args=(port,), daemon=True).start()
    return {"ok": True}


# --------------------------------------------------------------------------- проверка за интернет
def internet_worker():
    hosts = [("api.open-meteo.com", 443), ("tile.openstreetmap.org", 443)]
    while True:
        ok = 0
        for h, p in hosts:
            try:
                socket.create_connection((h, p), 3).close()
                ok += 1
            except OSError:
                pass
        with LOCK:
            ST["internet"] = {"ok": ok, "total": len(hosts), "ts": time.time()}
        time.sleep(25)


# --------------------------------------------------------------------------- генератор на настройки
def cli_val(v):
    v = str(v)
    if v == "" or re.search(r'[\s"=;\\]', v):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    return v


def cli_line(path, op, attrs, numbers=None):
    parts = [path + "/" + op]
    if numbers:
        parts.append("numbers=" + cli_val(numbers))
    parts += ["%s=%s" % (k, cli_val(v)) for k, v in attrs.items()]
    return " ".join(parts)


def upsert(path, key, attrs):
    return {"op": "upsert", "path": path, "key": key, "attrs": attrs,
            "cli": TR("# създава или обновява: ") + cli_line(path, "add", attrs)}


def setter(path, attrs, numbers=None):
    return {"op": "set", "path": path, "numbers": numbers, "attrs": attrs, "cli": cli_line(path, "set", attrs, numbers)}


class BadParam(ValueError):
    pass


def need(cond, msg):
    if not cond:
        raise BadParam(msg)


WIDTHS = {
    "wifi": {"20": "20mhz", "40": "20/40mhz", "80": "20/40/80mhz"},
    "wireless": {"20": "20mhz", "40": "20/40mhz-Ce", "80": "20/40/80mhz-Ceee"},
}
BANDS = {
    "wifi": {"5ghz-ax", "5ghz-ac", "5ghz-n", "2ghz-ax", "2ghz-n"},
    "wireless": {"5ghz-a/n/ac", "5ghz-onlyn", "5ghz-n/ac", "2ghz-b/g/n", "2ghz-onlyn"},
}


def build_link(p):
    pkg = p.get("pkg")
    need(pkg in ("wifi", "wireless"), TR("Изберете вид на пакета (wifi или wireless)."))
    role = p.get("role")
    need(role in ("ap", "client"), TR("Изберете роля."))
    iface = str(p.get("iface", "")).strip()
    need(re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", iface), TR("Невалидно име на интерфейс."))
    ssid = str(p.get("ssid", ""))
    need(1 <= len(ssid.encode("utf-8")) <= 32 and not re.search(r"[\x00-\x1f]", ssid), TR("SSID: 1-32 знака."))
    pw = str(p.get("passphrase", ""))
    need(8 <= len(pw) <= 63 and re.fullmatch(r"[\x20-\x7e]+", pw), TR("Паролата: 8-63 знака, само латински букви, цифри и символи."))
    try:
        freq = int(p.get("frequency"))
    except (TypeError, ValueError):
        raise BadParam(TR("Невалидна честота."))
    need(2400 <= freq <= 2500 or 4900 <= freq <= 6100, TR("Честотата е извън обхватите 2.4 и 5 GHz."))
    width = WIDTHS[pkg].get(str(p.get("width", "20")))
    need(width, TR("Невалидна ширина на канала."))
    band = str(p.get("band", ""))
    need(band in BANDS[pkg], TR("Невалиден обхват."))
    country = str(p.get("country", "Bulgaria")).strip()
    need(re.fullmatch(r"[A-Za-z ]{2,30}", country), TR("Държавата се въвежда на латиница (напр. Bulgaria)."))
    ident = str(p.get("identity", "")).strip()
    need(ident == "" or re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", ident), TR("Име на антената: латиница, цифри, - _ . (до 32 знака)."))
    ip = str(p.get("ip", "")).strip()
    gw = str(p.get("gateway", "")).strip()
    if ip:
        m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})", ip)
        need(m and all(int(x) <= 255 for x in m.groups()[:4]) and 8 <= int(m.group(5)) <= 30, TR("IP адрес: напр. 192.168.88.2/24."))
    if gw:
        need(re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", gw) and all(int(x) <= 255 for x in gw.split(".")), TR("Невалиден шлюз."))
    eth = str(p.get("eth", "ether1")).strip() or "ether1"
    need(re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", eth), TR("Невалидно име на кабелния порт."))
    acts = []
    if ident:
        acts.append(setter("/system/identity", {"name": ident}))
    if pkg == "wifi":
        acts.append(upsert("/interface/wifi/security", "name",
                           {"name": "af-sec", "authentication-types": "wpa2-psk", "passphrase": pw}))
        acts.append(upsert("/interface/wifi/channel", "name",
                           {"name": "af-ch", "frequency": str(freq), "width": width, "band": band}))
        acts.append(upsert("/interface/wifi/configuration", "name",
                           {"name": "af-cfg", "mode": "ap" if role == "ap" else "station-bridge", "ssid": ssid,
                            "country": country, "installation": "outdoor", "security": "af-sec",
                            "channel": "af-ch"}))
        acts.append(setter("/interface/wifi", {"configuration": "af-cfg", "disabled": "no"}, iface))
    else:
        acts.append(upsert("/interface/wireless/security-profiles", "name",
                           {"name": "af-sec", "mode": "dynamic-keys", "authentication-types": "wpa2-psk",
                            "wpa2-pre-shared-key": pw}))
        attrs = {"mode": "bridge" if role == "ap" else "station-bridge", "ssid": ssid, "band": band,
                 "frequency": str(freq), "channel-width": width, "country": country.lower(),
                 "security-profile": "af-sec", "wireless-protocol": "nv2" if p.get("nv2") else "802.11",
                 "installation": "outdoor", "distance": "dynamic", "disabled": "no"}
        if role == "client":
            attrs["scan-list"] = str(freq)
        acts.append(setter("/interface/wireless", attrs, iface))
    if ip:
        acts.append({"op": "bridge", "eth": eth, "iface": iface,
                     "cli": TR("# мост: ако %s вече е в мост - ползва се той, иначе: /interface/bridge/add name=bridge1\n"
                            "/interface/bridge/port/add bridge=<мост> interface=%s   # и %s") % (eth, eth, iface)})
        acts.append({"op": "ip", "address": ip, "clean": bool(p.get("clean")), "drop": True,
                     "cli": (TR("# изчиства DHCP сървър/клиент и излишни адреси\n") if p.get("clean") else "") +
                            TR("/ip/address/set address=%s interface=<мост>   # връзката може да прекъсне") % ip})
    if gw:
        acts.append(upsert("/ip/route", "dst-address", {"dst-address": "0.0.0.0/0", "gateway": gw}))
    return acts


def build_prepare(p):
    user = str(p.get("user", "alignfree")).strip()
    need(re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", user), TR("Потребител: 3-32 знака (латински букви, цифри, _ . -)."))
    pw = str(p.get("password", ""))
    need(len(pw) >= 8 and re.fullmatch(r"[\x20-\x7e]+", pw), TR("Паролата: поне 8 знака, само латински букви, цифри и символи."))
    allow = str(p.get("allow", "")).strip()
    need(allow == "" or re.fullmatch(r"[0-9./,]{7,64}", allow), TR("Разрешен адрес: напр. 192.168.88.10 или 192.168.88.0/24."))
    acts = [upsert("/user/group", "name", {"name": "alignfree", "policy": "read,api,test"}),
            upsert("/user", "name", {"name": user, "group": "alignfree", "password": pw})]
    svc = {"disabled": "no"}
    if allow:
        svc["address"] = allow
    acts.append(setter("/ip/service", svc, "api"))
    if p.get("bwserver", True):
        acts.append(setter("/tool/bandwidth-server", {"enabled": "yes", "authenticate": "yes"}))
    return acts


BUILDERS = {"link": build_link, "prepare": build_prepare}


def run_actions(api, actions):
    results = []
    ctx = {"bridge": "bridge1"}
    for a in actions:
        try:
            if a["op"] == "bridge":
                eth = a["eth"]
                rows, _ = api.talk("/interface/bridge/port/print", "?interface=" + eth)
                if rows:
                    ctx["bridge"] = rows[0].get("bridge", "bridge1")
                else:
                    br, _ = api.talk("/interface/bridge/print", "?name=bridge1")
                    if not br:
                        api.talk("/interface/bridge/add", "=name=bridge1")
                    api.talk("/interface/bridge/port/add", "=bridge=bridge1", "=interface=" + eth)
                rows, _ = api.talk("/interface/bridge/port/print", "?interface=" + a["iface"])
                if rows:
                    if rows[0].get("bridge") != ctx["bridge"]:
                        api.talk("/interface/bridge/port/set", "=.id=" + rows[0][".id"], "=bridge=" + ctx["bridge"])
                else:
                    api.talk("/interface/bridge/port/add", "=bridge=" + ctx["bridge"], "=interface=" + a["iface"])
            elif a["op"] == "ip":
                br = ctx["bridge"]
                if a["clean"]:
                    for path in ("/ip/dhcp-server", "/ip/dhcp-client"):
                        try:
                            old, _ = api.talk(path + "/print")
                            for r in old:
                                api.talk(path + "/remove", "=.id=" + r[".id"])
                        except RouterOSError:
                            pass
                rows, _ = api.talk("/ip/address/print")
                if any(r.get("address") == a["address"] and r.get("interface") == br for r in rows):
                    pass
                else:
                    target = next((r for r in rows if r.get("interface") == br), rows[0] if (rows and a["clean"]) else None)
                    if a["clean"]:
                        for r in rows:
                            if target is None or r[".id"] != target[".id"]:
                                api.talk("/ip/address/remove", "=.id=" + r[".id"])
                    if target is not None:
                        api.talk("/ip/address/set", "=.id=" + target[".id"], "=address=" + a["address"], "=interface=" + br)
                    else:
                        api.talk("/ip/address/add", "=address=" + a["address"], "=interface=" + br)
            elif a["op"] == "upsert":
                rows, _ = api.talk(a["path"] + "/print", "?%s=%s" % (a["key"], a["attrs"][a["key"]]))
                if rows:
                    attrs = ["=%s=%s" % (k, v) for k, v in a["attrs"].items() if k != a["key"]]
                    api.talk(a["path"] + "/set", "=.id=" + rows[0][".id"], *attrs)
                else:
                    api.talk(a["path"] + "/add", *["=%s=%s" % (k, v) for k, v in a["attrs"].items()])
            else:
                attrs = ["=%s=%s" % (k, v) for k, v in a["attrs"].items()]
                if a.get("numbers"):
                    attrs.insert(0, "=numbers=" + a["numbers"])
                api.talk(a["path"] + "/set", *attrs)
            results.append({"cli": a["cli"], "ok": True})
        except (RouterOSError, OSError, ConnectionError, KeyError) as e:
            if a.get("drop") and isinstance(e, (OSError, ConnectionError)):
                results.append({"cli": a["cli"], "ok": True, "note": TR("връзката прекъсна - това е нормално след смяна на адреса")})
                break
            results.append({"cli": a["cli"], "ok": False, "error": str(e) or e.__class__.__name__})
            break
    return results


def mask_cli(text, secrets_):
    for s in secrets_:
        if s:
            text = text.replace(cli_val(s), "********")
    return text


@route_post("/api/setup/preview")
def api_setup_preview(handler, data):
    builder = BUILDERS.get(data.get("kind"))
    if not builder:
        return {"ok": False, "error": TR("Непознат вид настройка.")}
    try:
        acts = builder(data.get("params") or {})
    except BadParam as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "commands": [a["cli"] for a in acts]}


@route_post("/api/setup/apply")
def api_setup_apply(handler, data):
    if data.get("confirm") is not True:
        return {"ok": False, "error": TR("Липсва потвърждение.")}
    builder = BUILDERS.get(data.get("kind"))
    if not builder:
        return {"ok": False, "error": TR("Непознат вид настройка.")}
    cfg = load_cfg()
    if cfg["demo"]:
        return {"ok": False, "error": TR("В демо режим не се променя нищо.")}
    try:
        acts = builder(data.get("params") or {})
    except BadParam as e:
        return {"ok": False, "error": str(e)}
    prefix = "router2" if data.get("target") == "far" else "router"
    api = None
    try:
        api = open_api(cfg, prefix, data.get("login") or None)
        pre = []
        if data.get("kind") == "link" and data.get("backup", True):
            name = "mikrotikalignfree-" + time.strftime("%Y%m%d-%H%M%S")
            try:
                api.talk("/system/backup/save", "=name=" + name)
                pre = [{"cli": TR("# резервно копие: %s.backup") % name, "ok": True}]
            except RouterOSError as e:
                return {"ok": False, "results": [{"cli": TR("# резервно копие"), "ok": False,
                                                   "error": TR("Копието не е направено, нищо не е променено: ") + str(e)}]}
        results = pre + run_actions(api, acts)
        return {"ok": all(r["ok"] for r in results), "results": results}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


@route_post("/api/setup/backup")
def api_setup_backup(handler, data):
    cfg = load_cfg()
    if cfg["demo"]:
        return {"ok": False, "error": TR("В демо режим не се прави копие.")}
    prefix = "router2" if data.get("target") == "far" else "router"
    name = "mikrotikalignfree-" + time.strftime("%Y%m%d-%H%M%S")
    api = None
    try:
        api = open_api(cfg, prefix)
        api.talk("/system/backup/save", "=name=" + name)
        exported = True
        try:
            api.talk("/export", "=file=" + name)
        except RouterOSError:
            exported = False
        return {"ok": True, "name": name, "export": exported}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


# --------------------------------------------------------------------------- мрежови настройки на компютъра (Windows)
def oem_decode(raw):
    try:
        import ctypes
        enc = "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except (ImportError, AttributeError, OSError):
        enc = "utf-8"
    for e in (enc, "utf-8", "cp1251"):
        try:
            return raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


@route_get("/api/netsh/adapters")
def api_netsh_adapters(handler, query):
    if os.name != "nt":
        return {"ok": False, "error": TR("Тази функция е само за Windows.")}
    try:
        out = subprocess.run(["netsh", "interface", "show", "interface"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    lines = oem_decode(out.stdout).splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("---")), -1)
    names = []
    for l in lines[start + 1:]:
        parts = l.split()
        if len(parts) >= 4:
            names.append(" ".join(parts[3:]))
    return {"ok": True, "adapters": names}


def run_netsh_elevated(args):
    quoted = args.replace("'", "''")
    cmd = ("$p = Start-Process -FilePath 'netsh.exe' -ArgumentList '%s' -Verb RunAs -Wait -PassThru "
           "-WindowStyle Hidden; exit $p.ExitCode" % quoted)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                       capture_output=True, timeout=120)
    return r.returncode


ADAPTER_RE = re.compile(r"[\w .()\-]{1,64}")
IP_RE = re.compile(r"(\d{1,3}\.){3}\d{1,3}")


@route_post("/api/netsh/set")
def api_netsh_set(handler, data):
    if os.name != "nt":
        return {"ok": False, "error": TR("Тази функция е само за Windows.")}
    name = str(data.get("name", ""))
    if not ADAPTER_RE.fullmatch(name):
        return {"ok": False, "error": TR("Невалидно име на адаптер.")}
    if data.get("dhcp"):
        args = 'interface ipv4 set address name="%s" source=dhcp' % name
    else:
        ip, mask, gw = str(data.get("ip", "")), str(data.get("mask", "255.255.255.0")), str(data.get("gateway", ""))
        if not (IP_RE.fullmatch(ip) and IP_RE.fullmatch(mask) and (not gw or IP_RE.fullmatch(gw))):
            return {"ok": False, "error": TR("Невалиден IP адрес, маска или шлюз.")}
        args = 'interface ipv4 set address name="%s" source=static address=%s mask=%s' % (name, ip, mask)
        if gw:
            args += " gateway=" + gw
    try:
        code = run_netsh_elevated(args)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": code == 0, "error": "" if code == 0 else TR("netsh върна код %s (отказан UAC или грешка).") % code,
            "command": "netsh " + args}


# --------------------------------------------------------------------------- вградена карта (Leaflet, BSD-2)
LEAFLET_JS_B64 = "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"
LEAFLET_CSS = r'''/* required styles */

.leaflet-pane,
.leaflet-tile,
.leaflet-marker-icon,
.leaflet-marker-shadow,
.leaflet-tile-container,
.leaflet-pane > svg,
.leaflet-pane > canvas,
.leaflet-zoom-box,
.leaflet-image-layer,
.leaflet-layer {
	position: absolute;
	left: 0;
	top: 0;
	}
.leaflet-container {
	overflow: hidden;
	}
.leaflet-tile,
.leaflet-marker-icon,
.leaflet-marker-shadow {
	-webkit-user-select: none;
	   -moz-user-select: none;
	        user-select: none;
	  -webkit-user-drag: none;
	}
/* Prevents IE11 from highlighting tiles in blue */
.leaflet-tile::selection {
	background: transparent;
}
/* Safari renders non-retina tile on retina better with this, but Chrome is worse */
.leaflet-safari .leaflet-tile {
	image-rendering: -webkit-optimize-contrast;
	}
/* hack that prevents hw layers "stretching" when loading new tiles */
.leaflet-safari .leaflet-tile-container {
	width: 1600px;
	height: 1600px;
	-webkit-transform-origin: 0 0;
	}
.leaflet-marker-icon,
.leaflet-marker-shadow {
	display: block;
	}
/* .leaflet-container svg: reset svg max-width decleration shipped in Joomla! (joomla.org) 3.x */
/* .leaflet-container img: map is broken in FF if you have max-width: 100% on tiles */
.leaflet-container .leaflet-overlay-pane svg {
	max-width: none !important;
	max-height: none !important;
	}
.leaflet-container .leaflet-marker-pane img,
.leaflet-container .leaflet-shadow-pane img,
.leaflet-container .leaflet-tile-pane img,
.leaflet-container img.leaflet-image-layer,
.leaflet-container .leaflet-tile {
	max-width: none !important;
	max-height: none !important;
	width: auto;
	padding: 0;
	}

.leaflet-container img.leaflet-tile {
	/* See: https://bugs.chromium.org/p/chromium/issues/detail?id=600120 */
	mix-blend-mode: plus-lighter;
}

.leaflet-container.leaflet-touch-zoom {
	-ms-touch-action: pan-x pan-y;
	touch-action: pan-x pan-y;
	}
.leaflet-container.leaflet-touch-drag {
	-ms-touch-action: pinch-zoom;
	/* Fallback for FF which doesn't support pinch-zoom */
	touch-action: none;
	touch-action: pinch-zoom;
}
.leaflet-container.leaflet-touch-drag.leaflet-touch-zoom {
	-ms-touch-action: none;
	touch-action: none;
}
.leaflet-container {
	-webkit-tap-highlight-color: transparent;
}
.leaflet-container a {
	-webkit-tap-highlight-color: rgba(51, 181, 229, 0.4);
}
.leaflet-tile {
	filter: inherit;
	visibility: hidden;
	}
.leaflet-tile-loaded {
	visibility: inherit;
	}
.leaflet-zoom-box {
	width: 0;
	height: 0;
	-moz-box-sizing: border-box;
	     box-sizing: border-box;
	z-index: 800;
	}
/* workaround for https://bugzilla.mozilla.org/show_bug.cgi?id=888319 */
.leaflet-overlay-pane svg {
	-moz-user-select: none;
	}

.leaflet-pane         { z-index: 400; }

.leaflet-tile-pane    { z-index: 200; }
.leaflet-overlay-pane { z-index: 400; }
.leaflet-shadow-pane  { z-index: 500; }
.leaflet-marker-pane  { z-index: 600; }
.leaflet-tooltip-pane   { z-index: 650; }
.leaflet-popup-pane   { z-index: 700; }

.leaflet-map-pane canvas { z-index: 100; }
.leaflet-map-pane svg    { z-index: 200; }

.leaflet-vml-shape {
	width: 1px;
	height: 1px;
	}
.lvml {
	behavior: url(#default#VML);
	display: inline-block;
	position: absolute;
	}


/* control positioning */

.leaflet-control {
	position: relative;
	z-index: 800;
	pointer-events: visiblePainted; /* IE 9-10 doesn't have auto */
	pointer-events: auto;
	}
.leaflet-top,
.leaflet-bottom {
	position: absolute;
	z-index: 1000;
	pointer-events: none;
	}
.leaflet-top {
	top: 0;
	}
.leaflet-right {
	right: 0;
	}
.leaflet-bottom {
	bottom: 0;
	}
.leaflet-left {
	left: 0;
	}
.leaflet-control {
	float: left;
	clear: both;
	}
.leaflet-right .leaflet-control {
	float: right;
	}
.leaflet-top .leaflet-control {
	margin-top: 10px;
	}
.leaflet-bottom .leaflet-control {
	margin-bottom: 10px;
	}
.leaflet-left .leaflet-control {
	margin-left: 10px;
	}
.leaflet-right .leaflet-control {
	margin-right: 10px;
	}


/* zoom and fade animations */

.leaflet-fade-anim .leaflet-popup {
	opacity: 0;
	-webkit-transition: opacity 0.2s linear;
	   -moz-transition: opacity 0.2s linear;
	        transition: opacity 0.2s linear;
	}
.leaflet-fade-anim .leaflet-map-pane .leaflet-popup {
	opacity: 1;
	}
.leaflet-zoom-animated {
	-webkit-transform-origin: 0 0;
	    -ms-transform-origin: 0 0;
	        transform-origin: 0 0;
	}
svg.leaflet-zoom-animated {
	will-change: transform;
}

.leaflet-zoom-anim .leaflet-zoom-animated {
	-webkit-transition: -webkit-transform 0.25s cubic-bezier(0,0,0.25,1);
	   -moz-transition:    -moz-transform 0.25s cubic-bezier(0,0,0.25,1);
	        transition:         transform 0.25s cubic-bezier(0,0,0.25,1);
	}
.leaflet-zoom-anim .leaflet-tile,
.leaflet-pan-anim .leaflet-tile {
	-webkit-transition: none;
	   -moz-transition: none;
	        transition: none;
	}

.leaflet-zoom-anim .leaflet-zoom-hide {
	visibility: hidden;
	}


/* cursors */

.leaflet-interactive {
	cursor: pointer;
	}
.leaflet-grab {
	cursor: -webkit-grab;
	cursor:    -moz-grab;
	cursor:         grab;
	}
.leaflet-crosshair,
.leaflet-crosshair .leaflet-interactive {
	cursor: crosshair;
	}
.leaflet-popup-pane,
.leaflet-control {
	cursor: auto;
	}
.leaflet-dragging .leaflet-grab,
.leaflet-dragging .leaflet-grab .leaflet-interactive,
.leaflet-dragging .leaflet-marker-draggable {
	cursor: move;
	cursor: -webkit-grabbing;
	cursor:    -moz-grabbing;
	cursor:         grabbing;
	}

/* marker & overlays interactivity */
.leaflet-marker-icon,
.leaflet-marker-shadow,
.leaflet-image-layer,
.leaflet-pane > svg path,
.leaflet-tile-container {
	pointer-events: none;
	}

.leaflet-marker-icon.leaflet-interactive,
.leaflet-image-layer.leaflet-interactive,
.leaflet-pane > svg path.leaflet-interactive,
svg.leaflet-image-layer.leaflet-interactive path {
	pointer-events: visiblePainted; /* IE 9-10 doesn't have auto */
	pointer-events: auto;
	}

/* visual tweaks */

.leaflet-container {
	background: #ddd;
	outline-offset: 1px;
	}
.leaflet-container a {
	color: #0078A8;
	}
.leaflet-zoom-box {
	border: 2px dotted #38f;
	background: rgba(255,255,255,0.5);
	}


/* general typography */
.leaflet-container {
	font-family: "Helvetica Neue", Arial, Helvetica, sans-serif;
	font-size: 12px;
	font-size: 0.75rem;
	line-height: 1.5;
	}


/* general toolbar styles */

.leaflet-bar {
	box-shadow: 0 1px 5px rgba(0,0,0,0.65);
	border-radius: 4px;
	}
.leaflet-bar a {
	background-color: #fff;
	border-bottom: 1px solid #ccc;
	width: 26px;
	height: 26px;
	line-height: 26px;
	display: block;
	text-align: center;
	text-decoration: none;
	color: black;
	}
.leaflet-bar a,
.leaflet-control-layers-toggle {
	background-position: 50% 50%;
	background-repeat: no-repeat;
	display: block;
	}
.leaflet-bar a:hover,
.leaflet-bar a:focus {
	background-color: #f4f4f4;
	}
.leaflet-bar a:first-child {
	border-top-left-radius: 4px;
	border-top-right-radius: 4px;
	}
.leaflet-bar a:last-child {
	border-bottom-left-radius: 4px;
	border-bottom-right-radius: 4px;
	border-bottom: none;
	}
.leaflet-bar a.leaflet-disabled {
	cursor: default;
	background-color: #f4f4f4;
	color: #bbb;
	}

.leaflet-touch .leaflet-bar a {
	width: 30px;
	height: 30px;
	line-height: 30px;
	}
.leaflet-touch .leaflet-bar a:first-child {
	border-top-left-radius: 2px;
	border-top-right-radius: 2px;
	}
.leaflet-touch .leaflet-bar a:last-child {
	border-bottom-left-radius: 2px;
	border-bottom-right-radius: 2px;
	}

/* zoom control */

.leaflet-control-zoom-in,
.leaflet-control-zoom-out {
	font: bold 18px 'Lucida Console', Monaco, monospace;
	text-indent: 1px;
	}

.leaflet-touch .leaflet-control-zoom-in, .leaflet-touch .leaflet-control-zoom-out  {
	font-size: 22px;
	}


/* layers control */

.leaflet-control-layers {
	box-shadow: 0 1px 5px rgba(0,0,0,0.4);
	background: #fff;
	border-radius: 5px;
	}
.leaflet-control-layers-toggle {
	background-image: url(images/layers.png);
	width: 36px;
	height: 36px;
	}
.leaflet-retina .leaflet-control-layers-toggle {
	background-image: url(images/layers-2x.png);
	background-size: 26px 26px;
	}
.leaflet-touch .leaflet-control-layers-toggle {
	width: 44px;
	height: 44px;
	}
.leaflet-control-layers .leaflet-control-layers-list,
.leaflet-control-layers-expanded .leaflet-control-layers-toggle {
	display: none;
	}
.leaflet-control-layers-expanded .leaflet-control-layers-list {
	display: block;
	position: relative;
	}
.leaflet-control-layers-expanded {
	padding: 6px 10px 6px 6px;
	color: #333;
	background: #fff;
	}
.leaflet-control-layers-scrollbar {
	overflow-y: scroll;
	overflow-x: hidden;
	padding-right: 5px;
	}
.leaflet-control-layers-selector {
	margin-top: 2px;
	position: relative;
	top: 1px;
	}
.leaflet-control-layers label {
	display: block;
	font-size: 13px;
	font-size: 1.08333em;
	}
.leaflet-control-layers-separator {
	height: 0;
	border-top: 1px solid #ddd;
	margin: 5px -10px 5px -6px;
	}

/* Default icon URLs */
.leaflet-default-icon-path { /* used only in path-guessing heuristic, see L.Icon.Default */
	background-image: url(images/marker-icon.png);
	}


/* attribution and scale controls */

.leaflet-container .leaflet-control-attribution {
	background: #fff;
	background: rgba(255, 255, 255, 0.8);
	margin: 0;
	}
.leaflet-control-attribution,
.leaflet-control-scale-line {
	padding: 0 5px;
	color: #333;
	line-height: 1.4;
	}
.leaflet-control-attribution a {
	text-decoration: none;
	}
.leaflet-control-attribution a:hover,
.leaflet-control-attribution a:focus {
	text-decoration: underline;
	}
.leaflet-attribution-flag {
	display: inline !important;
	vertical-align: baseline !important;
	width: 1em;
	height: 0.6669em;
	}
.leaflet-left .leaflet-control-scale {
	margin-left: 5px;
	}
.leaflet-bottom .leaflet-control-scale {
	margin-bottom: 5px;
	}
.leaflet-control-scale-line {
	border: 2px solid #777;
	border-top: none;
	line-height: 1.1;
	padding: 2px 5px 1px;
	white-space: nowrap;
	-moz-box-sizing: border-box;
	     box-sizing: border-box;
	background: rgba(255, 255, 255, 0.8);
	text-shadow: 1px 1px #fff;
	}
.leaflet-control-scale-line:not(:first-child) {
	border-top: 2px solid #777;
	border-bottom: none;
	margin-top: -2px;
	}
.leaflet-control-scale-line:not(:first-child):not(:last-child) {
	border-bottom: 2px solid #777;
	}

.leaflet-touch .leaflet-control-attribution,
.leaflet-touch .leaflet-control-layers,
.leaflet-touch .leaflet-bar {
	box-shadow: none;
	}
.leaflet-touch .leaflet-control-layers,
.leaflet-touch .leaflet-bar {
	border: 2px solid rgba(0,0,0,0.2);
	background-clip: padding-box;
	}


/* popup */

.leaflet-popup {
	position: absolute;
	text-align: center;
	margin-bottom: 20px;
	}
.leaflet-popup-content-wrapper {
	padding: 1px;
	text-align: left;
	border-radius: 12px;
	}
.leaflet-popup-content {
	margin: 13px 24px 13px 20px;
	line-height: 1.3;
	font-size: 13px;
	font-size: 1.08333em;
	min-height: 1px;
	}
.leaflet-popup-content p {
	margin: 17px 0;
	margin: 1.3em 0;
	}
.leaflet-popup-tip-container {
	width: 40px;
	height: 20px;
	position: absolute;
	left: 50%;
	margin-top: -1px;
	margin-left: -20px;
	overflow: hidden;
	pointer-events: none;
	}
.leaflet-popup-tip {
	width: 17px;
	height: 17px;
	padding: 1px;

	margin: -10px auto 0;
	pointer-events: auto;

	-webkit-transform: rotate(45deg);
	   -moz-transform: rotate(45deg);
	    -ms-transform: rotate(45deg);
	        transform: rotate(45deg);
	}
.leaflet-popup-content-wrapper,
.leaflet-popup-tip {
	background: white;
	color: #333;
	box-shadow: 0 3px 14px rgba(0,0,0,0.4);
	}
.leaflet-container a.leaflet-popup-close-button {
	position: absolute;
	top: 0;
	right: 0;
	border: none;
	text-align: center;
	width: 24px;
	height: 24px;
	font: 16px/24px Tahoma, Verdana, sans-serif;
	color: #757575;
	text-decoration: none;
	background: transparent;
	}
.leaflet-container a.leaflet-popup-close-button:hover,
.leaflet-container a.leaflet-popup-close-button:focus {
	color: #585858;
	}
.leaflet-popup-scrolled {
	overflow: auto;
	}

.leaflet-oldie .leaflet-popup-content-wrapper {
	-ms-zoom: 1;
	}
.leaflet-oldie .leaflet-popup-tip {
	width: 24px;
	margin: 0 auto;

	-ms-filter: "progid:DXImageTransform.Microsoft.Matrix(M11=0.70710678, M12=0.70710678, M21=-0.70710678, M22=0.70710678)";
	filter: progid:DXImageTransform.Microsoft.Matrix(M11=0.70710678, M12=0.70710678, M21=-0.70710678, M22=0.70710678);
	}

.leaflet-oldie .leaflet-control-zoom,
.leaflet-oldie .leaflet-control-layers,
.leaflet-oldie .leaflet-popup-content-wrapper,
.leaflet-oldie .leaflet-popup-tip {
	border: 1px solid #999;
	}


/* div icon */

.leaflet-div-icon {
	background: #fff;
	border: 1px solid #666;
	}


/* Tooltip */
/* Base styles for the element that has a tooltip */
.leaflet-tooltip {
	position: absolute;
	padding: 6px;
	background-color: #fff;
	border: 1px solid #fff;
	border-radius: 3px;
	color: #222;
	white-space: nowrap;
	-webkit-user-select: none;
	-moz-user-select: none;
	-ms-user-select: none;
	user-select: none;
	pointer-events: none;
	box-shadow: 0 1px 3px rgba(0,0,0,0.4);
	}
.leaflet-tooltip.leaflet-interactive {
	cursor: pointer;
	pointer-events: auto;
	}
.leaflet-tooltip-top:before,
.leaflet-tooltip-bottom:before,
.leaflet-tooltip-left:before,
.leaflet-tooltip-right:before {
	position: absolute;
	pointer-events: none;
	border: 6px solid transparent;
	background: transparent;
	content: "";
	}

/* Directions */

.leaflet-tooltip-bottom {
	margin-top: 6px;
}
.leaflet-tooltip-top {
	margin-top: -6px;
}
.leaflet-tooltip-bottom:before,
.leaflet-tooltip-top:before {
	left: 50%;
	margin-left: -6px;
	}
.leaflet-tooltip-top:before {
	bottom: 0;
	margin-bottom: -12px;
	border-top-color: #fff;
	}
.leaflet-tooltip-bottom:before {
	top: 0;
	margin-top: -12px;
	margin-left: -6px;
	border-bottom-color: #fff;
	}
.leaflet-tooltip-left {
	margin-left: -6px;
}
.leaflet-tooltip-right {
	margin-left: 6px;
}
.leaflet-tooltip-left:before,
.leaflet-tooltip-right:before {
	top: 50%;
	margin-top: -6px;
	}
.leaflet-tooltip-left:before {
	right: 0;
	margin-right: -12px;
	border-left-color: #fff;
	}
.leaflet-tooltip-right:before {
	left: 0;
	margin-left: -12px;
	border-right-color: #fff;
	}

/* Printing */

@media print {
	/* Prevent printers from removing background-images of controls. */
	.leaflet-control {
		-webkit-print-color-adjust: exact;
		print-color-adjust: exact;
		}
	}
'''
_STATIC = {}


def static_asset(name):
    if name not in _STATIC:
        if name == "leaflet.js":
            _STATIC[name] = (gzip.decompress(base64.b64decode(LEAFLET_JS_B64)), "application/javascript; charset=utf-8")
        elif name == "leaflet.css":
            _STATIC[name] = (LEAFLET_CSS.encode("utf-8"), "text/css; charset=utf-8")
        else:
            return None
    return _STATIC[name]


def start_background():
    threading.Thread(target=internet_worker, daemon=True).start()
    threading.Thread(target=mndp_worker, daemon=True).start()


def main():
    cfg = load_cfg()
    changed = False
    if not clean_room(cfg["room"]):
        cfg["room"] = secrets.token_urlsafe(9)
        changed = True
    if changed or not os.path.exists(CFG_PATH):
        save_cfg(cfg)
    port = int(cfg.get("http_port", 8080))
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    url = "http://127.0.0.1:%d" % port
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # вече е стартирана - само отваряме страницата
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    RouterPoller("local", "router").start()
    RouterPoller("far", "router2").start()
    MqttSync().start()
    start_background()
    print(TR("MikrotikAlignFree %s - отворете %s (Ctrl+C за изход)") % (APP_VERSION, url))
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------- уеб интерфейс
# Leaflet 1.9.4 (c) Volodymyr Agafonkin, BSD-2-Clause - вграден за карта без интернет зависимост за самата библиотека.
PAGE = r'''<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MikrotikAlignFree</title>
<link rel="stylesheet" href="/static/leaflet.css">
<style>
:root{--bg:#f3f4f6;--sf:#fff;--sf2:#eceff3;--tx:#13161b;--mu:#5a6371;--bd:#d5d9e0;--ac:#1f6feb;--g:#12823a;--y:#a86b00;--o:#c2410c;--r:#c42b2b;--info:#e6efff;--warn:#fff1dc;--err:#fde8e8;color-scheme:light}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e1115;--sf:#161a20;--sf2:#1e242c;--tx:#e9ecf1;--mu:#98a2b2;--bd:#2b323c;--ac:#5aa2ff;--g:#3ccb67;--y:#f2b632;--o:#ff8f52;--r:#ff5d5d;--info:#13253f;--warn:#3a2a10;--err:#3d1717;color-scheme:dark}}
:root[data-theme=dark]{--bg:#0e1115;--sf:#161a20;--sf2:#1e242c;--tx:#e9ecf1;--mu:#98a2b2;--bd:#2b323c;--ac:#5aa2ff;--g:#3ccb67;--y:#f2b632;--o:#ff8f52;--r:#ff5d5d;--info:#13253f;--warn:#3a2a10;--err:#3d1717;color-scheme:dark}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;padding:10px 16px;background:var(--sf);border-bottom:1px solid var(--bd);position:sticky;top:0;z-index:500}
h1{font-size:18px;margin:0 8px 0 0}.sp{flex:1}
.btn,nav button,.seg button{background:var(--sf2);color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit}
.btn:hover,nav button:hover,.seg button:hover{border-color:var(--ac)}
.btn.pri,nav button.on,.seg button.on{background:var(--ac);color:#fff;border-color:var(--ac)}
.btn.dng{border-color:var(--r);color:var(--r)}.btn:disabled{opacity:.5;cursor:default}
nav{display:flex;flex-wrap:wrap;gap:6px;background:var(--sf2);padding:4px;border-radius:12px}nav button{border-color:transparent;background:transparent}
.seg{display:inline-flex}.seg button{border-radius:0}.seg button:first-child{border-radius:8px 0 0 8px}.seg button:last-child{border-radius:0 8px 8px 0}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 16px;background:var(--bg)}
.toolbar select{width:auto}
main{max-width:1120px;margin:0 auto;padding:14px 16px 60px}
.card{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-bottom:14px}
.card h3{margin:0 0 6px;font-size:16px}.card h4{margin:12px 0 4px;font-size:14px}
.pill{font-size:13px;border:1px solid var(--bd);border-radius:999px;padding:2px 10px;color:var(--mu);background:var(--sf)}
.pill.ok{color:var(--g);border-color:var(--g)}.pill.bad{color:var(--r);border-color:var(--r)}.pill.warn{color:var(--y);border-color:var(--y)}
.big{font-size:88px;font-weight:700;line-height:1;text-align:center;margin:4px 0}
.mid{font-size:40px;font-weight:700;line-height:1.1}.note{font-size:13px;color:var(--mu)}.hide{display:none!important}
.bar{height:18px;border-radius:9px;background:var(--bd);overflow:hidden}.bar i{display:block;height:100%;width:0;background:var(--g);transition:width .25s}
.row{display:flex;flex-wrap:wrap;gap:6px 18px;justify-content:space-between;margin-top:10px;color:var(--mu)}.row b{color:var(--tx)}
label{display:block;font-size:13px;color:var(--mu);margin:8px 0 3px}
input,select,textarea{width:100%;padding:7px 9px;border:1px solid var(--bd);border-radius:8px;background:var(--bg);color:var(--tx);font:inherit}
input[type=checkbox],input[type=radio]{width:auto}textarea{font:13px/1.4 Consolas,monospace;min-height:110px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}.big{font-size:64px}}
.two{display:flex;gap:12px;flex-wrap:wrap}.two>div{flex:1;min-width:190px;text-align:center}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}th{color:var(--mu);font-weight:500}
canvas{width:100%;background:var(--bg);border:1px solid var(--bd);border-radius:8px;display:block}
.banner{border-radius:12px;padding:14px 16px;margin-bottom:14px;border:1px solid var(--bd);background:var(--info)}
.banner.warn{background:var(--warn)}.banner.err{background:var(--err)}
.banner ol,.banner ul{margin:6px 0 6px 20px;padding:0}
#map{height:430px;border-radius:10px;border:1px solid var(--bd)}
.pin{width:28px;height:28px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;border:2px solid #fff;box-shadow:0 1px 4px #0006}
.pin span{transform:rotate(45deg)}
.chat{max-height:170px;overflow:auto;background:var(--bg);border:1px solid var(--bd);border-radius:8px;padding:6px 10px;margin:8px 0}
.chat div{padding:2px 0}.chat .me{color:var(--ac)}
.tag{display:inline-block;font-size:12px;border-radius:6px;padding:1px 7px;background:var(--sf2);border:1px solid var(--bd);color:var(--mu)}
details{border:1px solid var(--bd);border-radius:10px;margin:8px 0;background:var(--sf)}summary{cursor:pointer;padding:10px 14px;font-weight:600}
details>div{padding:0 16px 12px}details li{margin:4px 0}
code,kbd{background:var(--sf2);border:1px solid var(--bd);border-radius:5px;padding:0 5px;font:13px Consolas,monospace}
.ok{color:var(--g)}.badc{color:var(--r)}.chips{display:flex;flex-wrap:wrap;gap:6px}
#tip{position:fixed;pointer-events:none;background:#000d;color:#fff;font-size:12px;padding:4px 8px;border-radius:6px;z-index:900}

.sumbar{display:flex;flex-wrap:wrap;gap:6px 20px;align-items:center;background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:8px 14px;margin-bottom:12px}
.sumbar b{font-size:17px}.sumbar .note{margin-right:-12px}
.pan h4{margin:0 0 6px;letter-spacing:.06em;font-size:13px}.pan.l{border-top:3px solid var(--ac)}.pan.r{border-top:3px solid var(--o)}
.alert{border-radius:8px;padding:8px 12px;background:var(--info);margin:6px 0;font-size:14px}.alert.err{background:var(--err)}
.sigrow{display:flex;align-items:flex-end;gap:8px;margin:6px 0}.sigrow .peakd{margin-left:auto;text-align:right}.sigrow .peakd b{font-size:24px}
.big2{font-size:68px;font-weight:700;line-height:1}.unit{font-size:20px;color:var(--mu);margin-bottom:6px}
.kv{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dashed var(--bd);font-size:14px}.kv span{color:var(--mu)}
pre.cmd{background:var(--sf2);border:1px solid var(--bd);border-radius:8px;padding:8px 10px;font:12.5px/1.45 Consolas,monospace;white-space:pre-wrap;word-break:break-all;margin:6px 0}
.step{display:inline-flex;width:24px;height:24px;border-radius:50%;background:var(--ac);color:#fff;align-items:center;justify-content:center;font-size:13px;margin-right:8px}
footer{text-align:center;color:var(--mu);font-size:12px;padding:20px}
@media print{header,.toolbar,nav{display:none}}
</style></head><body>
<header>
 <h1>MikrotikAlignFree</h1>
 <nav id="nav">
  <button data-t="aim" class="on">Насочване</button><button data-t="plan">План и карта</button>
  <button data-t="setup">Настройка на антената</button><button data-t="report">Отчет</button>
  <button data-t="guide">Инструкции</button>
 </nav>
 <span id="conn" class="pill">...</span><span id="far-pill" class="pill hide"></span><span id="room-chip" class="pill hide"></span>
 <span id="net-pill" class="pill hide"></span><span id="mq-pill" class="pill hide"></span>
 <span class="sp"></span>
 <button class="btn" id="b-lang" title="Language / Език">🌐 <b id="b-lang-t"></b></button>
 <button class="btn" id="b-theme" title="Тема: автоматична / тъмна / светла">◐</button>
 <button class="btn" data-t="set" id="b-set">Настройки</button>
 <button class="btn" id="b-quit" title="Спира програмата">Изход</button>
</header>
<div class="toolbar" id="toolbar">
 <span class="note">Този лаптоп е при:</span>
 <span class="seg" id="side-seg"><button data-s="a" class="on" title="Вие сте при точка A">А</button><button data-s="b" title="Вие сте при точка B">Б</button></span>
 <button class="btn" id="b-aim" title="Казва на другия компютър да не пипа антената, докато насочвате">Аз насочвам</button>
 <button class="btn" id="b-sound" title="Превключва: изключен / тон / говор (клавиш M)">Звук: изкл.</button>
 <select id="mode" title="По кой сигнал се води индикацията"><option value="mine">по моя сигнал</option><option value="remote">по сигнала на другата страна</option><option value="min">по по-слабия от двата</option></select>
 <button class="btn" id="b-peak" title="Нулира пика (клавиш P)">Нулирай пик</button>
 <button class="btn" id="b-rec" title="Записва сигнала всяка секунда за CSV">Запис: изкл.</button>
 <button class="btn" id="b-point" title="Запазва текущата стойност като точка за отчета (клавиш S)">Запази точка</button>
 <button class="btn" id="b-help" title="Отваря инструкциите">Помощ</button>
</div>
<main>

<!-- ====================== НАСОЧВАНЕ ====================== -->
<section id="t-aim">
 <div class="sumbar hide" id="sumbar"></div>
 <div class="banner warn hide" id="cb">
  <div id="cb-main"></div>
  <div id="cb-login" class="hide" style="gap:8px;flex-wrap:wrap;margin:8px 0"><input id="cb-user" style="width:170px" placeholder="потребител" value="admin"><input id="cb-pass" type="password" style="width:210px" placeholder="парола"><button class="btn pri" id="cb-go">Свържи</button></div>
  <div id="cb-neigh" style="margin:8px 0"></div>
  <ol id="cb-steps" class="note"><li>Компютърът трябва да е в същата мрежа като антената. Помощник за мрежата има в „Настройка на антената“.</li><li>В RouterOS трябва да е включена API услугата (порт 8728): <code>/ip service enable api</code></li><li>Отделен потребител само с права <code>read, api, test</code> създава „Настройка на антената → Допълнително“.</li></ol>
  <button class="btn pri" id="ob-set">Към настройките</button> <button class="btn" id="ob-scan">Търси по IP адреси</button>
  <button class="btn hide" id="cb-auto">Настрой лаптопа автоматично</button>
  <button class="btn" id="ob-demo">Пробвай в демо режим</button> <button class="btn" id="ob-guide">Инструкции</button>
  <div id="ob-found" class="note" style="margin-top:8px"></div>
 </div>
 <div class="banner err hide" id="aimwarn"><b>Другата страна насочва в момента - не пипайте антената!</b></div>
 <div class="banner hide" id="planin"></div>

 <div class="grid2">
  <div class="card pan l"><h4 style="color:var(--ac)">ТУК</h4><div class="alert hide" id="l-alert"></div>
   <div class="sigrow"><div class="big2" id="l-sig">--</div><span class="unit">dBm</span><div class="peakd"><b id="l-dpk">--</b><div class="note">dB от пика</div></div></div>
   <div class="bar"><i id="l-bar"></i></div><div class="note" id="l-exp" style="margin:4px 0 8px"></div>
   <div id="l-rows"></div><div class="note" id="l-age" style="margin-top:6px"></div></div>
  <div class="card pan r"><h4 style="color:var(--o)">ОТСРЕЩА</h4><div class="alert hide" id="r-alert"></div>
   <div class="sigrow"><div class="big2" id="r-sig">--</div><span class="unit">dBm</span><div class="peakd"><b id="r-dpk">--</b><div class="note">dB от пика</div></div></div>
   <div class="bar"><i id="r-bar"></i></div><div class="note" id="r-exp" style="margin:4px 0 8px"></div>
   <div id="r-rows"></div><div class="note" id="r-age" style="margin-top:6px"></div></div>
 </div>

 <div class="card"><h3>Разговор с другия компютър</h3><span class="note" id="chat-state"></span>
  <div class="chat" id="chatlog" style="max-height:200px"><span class="note">Още няма съобщения.</span></div>
  <div class="chips" id="quick"></div>
  <div style="display:flex;gap:8px;margin-top:8px"><input id="chat-text" maxlength="200" placeholder="Съобщение до другия компютър..."><button class="btn" id="chat-send">Изпрати</button><button class="btn" id="b-talk" title="Задръжте (или клавиш Интервал) и говорете">🎤 Задръж и говори</button></div>
  <p class="note">Бързите бутони и текстът стигат отсреща веднага; ✓ значи, че другият компютър го е получил. За глас задръжте бутона 🎤 (или клавиша Интервал, когато не пишете в поле), говорете и пуснете - отсреща се чува след около секунда. Първия път браузърът пита за микрофона - натиснете „Разреши“.</p>
  <label><input type="checkbox" id="chat-speak" checked> Чети на глас получените текстови съобщения</label></div>

 <div class="grid2">
  <div class="card"><h3>История на сигнала <span class="note">(последните 3 минути)</span></h3>
   <canvas id="hist" width="520" height="200" style="height:200px"></canvas>
   <div class="note" style="margin-top:4px"><b style="color:#1f6feb">—</b> тук приема · <b style="color:#c2410c">—</b> отсреща приема · пунктир = пик тук</div></div>
  <div class="card"><h3>Целта за моята страна</h3>
   <div id="target"><span class="note">Изчислете плана в „План и карта“, за да видите целевия азимут и наклон.</span></div>
   <div class="grid" style="margin-top:8px">
    <div><label>Компасът ми показва сега, °</label><input id="heading" type="number" step="0.1" min="0" max="360"></div>
    <div><label>Инклинометърът показва, °</label><input id="tilt-now" type="number" step="0.1"></div></div>
   <div id="turn" class="note" style="margin-top:6px"></div></div>
 </div>
 <div class="card"><h3>Свързани устройства (регистрационна таблица)</h3>
  <div id="peers"><span class="note">Няма свързани устройства в регистрационната таблица.</span></div></div>
</section>

<!-- ====================== ПЛАН И КАРТА ====================== -->
<section id="t-plan" class="hide">
 <div class="grid2">
  <div>
   <div class="card"><b>Точка A</b>
    <div class="grid"><div><label>Име</label><input id="a-name" value="A"></div></div>
    <label>Координати (или линк от Google Maps)</label><input id="a-ll" value="41.8485, 23.1117">
    <p><button class="btn" data-pickbtn="a">Посочи на картата</button> <button class="btn" data-herebtn="a">Тук съм</button></p>
    <div class="grid">
     <div><label>Терен, н.в. (m) - празно = автоматично</label><input id="a-elev" type="number" placeholder="авто"></div>
     <div><label>Антена над терена, m</label><input id="a-mast" type="number" value="6"></div>
     <div><label>Модел</label><select id="a-dev"></select></div>
     <div><label>Усилване, dBi</label><input id="a-gain" type="number" step="0.5" value="16"></div>
     <div><label>TX мощност, dBm</label><input id="a-tx" type="number" value="14"></div>
     <div><label>Ширина на лъча, ° (празно = от модела)</label><input id="a-beam" type="number" placeholder="авто"></div>
    </div></div>
   <div class="card"><b>Точка B</b>
    <div class="grid"><div><label>Име</label><input id="b-name" value="B"></div></div>
    <label>Координати (или линк от Google Maps)</label><input id="b-ll" value="41.8245, 23.0749">
    <p><button class="btn" data-pickbtn="b">Посочи на картата</button> <button class="btn" data-herebtn="b">Тук съм</button></p>
    <div class="grid">
     <div><label>Терен, н.в. (m) - празно = автоматично</label><input id="b-elev" type="number" placeholder="авто"></div>
     <div><label>Антена над терена, m</label><input id="b-mast" type="number" value="3"></div>
     <div><label>Модел</label><select id="b-dev"></select></div>
     <div><label>Усилване, dBi</label><input id="b-gain" type="number" step="0.5" value="16"></div>
     <div><label>TX мощност, dBm</label><input id="b-tx" type="number" value="14"></div>
     <div><label>Ширина на лъча, ° (празно = от модела)</label><input id="b-beam" type="number" placeholder="авто"></div>
    </div></div>
   <div class="note" style="margin-bottom:10px">Посочете двете точки (на картата, с „Тук съм“ или с координати). Така екранът „Насочване“ показва посоката и очаквания сигнал за вашата страна. Данните за моделите са ориентировъчни - проверете ги в datasheet на производителя.</div>
   <div class="card"><b>Връзка</b>
    <div class="grid">
     <div><label>Честота, MHz</label><input id="freq" type="number" value="5500"></div>
     <div><label>Ширина на канала, MHz</label><select id="width"><option>20</option><option>40</option><option>80</option></select></div>
     <div><label>Допълнителни загуби, dB</label><input id="loss" type="number" value="2"></div>
     <div><label>Дървета/сгради по трасето, m</label><input id="clutter" type="number" value="0"></div>
     <div><label>Пречупване в атмосферата</label><select id="kf"><option value="1.33">Стандартно (k=1.33)</option><option value="1">Без пречупване (k=1)</option><option value="2">Силно (k=2)</option><option value="0.67">Отрицателно, мъгла (k=0.67)</option></select></div>
     <div><label>Магнитно отклонение, ° (изток +)</label><input id="decl" type="number" step="0.01" value="5.5"></div>
    </div>
    <label><input type="checkbox" id="decl-auto" checked> Автоматично по модела WMM2025 (работи без интернет)</label>
    <p><button class="btn pri" id="b-calc">Изчисли</button> <button class="btn" id="b-relief" title="Изтегля релефа отново и преизчислява">Обнови релефа</button> <span class="note" id="calc-msg"></span></p></div>
  </div>
  <div>
   <div class="card">
    <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px">
     <span class="note">Щракване върху картата задава:</span>
     <span class="seg" id="pick-seg"><button data-p="a" class="on">точка A</button><button data-p="b">точка B</button></span>
     <span class="seg" id="lay-seg"><button data-l="osm" class="on">Карта</button><button data-l="topo">Релеф</button><button data-l="sat">Сателит</button></span>
     <button class="btn" id="b-loc" title="Задава избраната точка по местоположението на устройството">Моята позиция</button>
     <button class="btn" id="b-fit">Покажи трасето</button>
    </div>
    <div id="map"><div class="note" style="padding:20px">Картата се зарежда...</div></div>
    <div class="note" style="margin-top:6px" id="map-note">Маркерите се местят с влачене. Сините/оранжевите сектори показват посоката и ширината на лъча. Подложките на картата изискват интернет.</div>
   </div>
   <div class="card"><b>Проекти</b>
    <div class="grid" style="align-items:end">
     <div><label>Име на проект</label><input id="proj-name" placeholder="напр. Клиент - Склад"></div>
     <div><label>Запазени</label><select id="proj-list"></select></div>
    </div>
    <p><button class="btn" id="proj-save">Запази</button> <button class="btn" id="proj-load">Зареди</button>
    <button class="btn dng" id="proj-del">Изтрий</button> <button class="btn" id="proj-exp">Експорт (JSON)</button>
    <label class="btn" style="display:inline-block;margin:0">Импорт <input type="file" id="proj-imp" accept=".json" class="hide"></label></p></div>
   <div class="card"><b>Сподели</b><p class="note">Изпраща въведените данни (точки, височини, модели, честота) на другия компютър през синхронизацията, за да имате еднакви числа от двете страни.</p>
    <button class="btn" id="plan-send">Изпрати плана на другия компютър</button> <span class="note" id="plan-send-msg"></span></div>
  </div>
 </div>
 <div id="res"><div class="card"><span class="note">Въведете точките (или ги посочете на картата) и натиснете „Изчисли“. Релефът се взема от Open-Meteo (Copernicus DEM, 90 m) и изисква интернет. Той не включва дървета и сгради - ползвайте полето „Дървета/сгради по трасето“.</span></div></div>
 <div class="card"><canvas id="cv" width="1040" height="300" style="height:300px"></canvas>
  <div class="note" style="margin-top:4px"><span style="color:#b59a6a">■</span> терен (с кривината на Земята) · <span style="color:#3b9a4a">■</span> дървета/сгради · <b style="color:#1f6feb">—</b> линия на видимост · <span style="color:#8fb4f0">■</span> 1-ва зона на Френел · <span style="color:#c2410c">- -</span> 60% от зоната</div></div>
</section>

<!-- ====================== НАСТРОЙКА НА АНТЕНАТА ====================== -->
<section id="t-setup" class="hide">
 <h2 style="margin:0 0 6px">Настройка на антената и тест на скоростта</h2>
 <p class="note">Тук програмата настройва антената вместо вас - без WinBox и без команди. Правете го у дома, на бюрото, за всяка антена поотделно (с този или с другия компютър). Отнема 1-3 минути на антена. Следвайте стъпките отгоре надолу.</p>

 <div class="card"><h3><span class="step">1</span>Свържете антената</h3>
  <ol><li>Порт <b>PoE</b> на адаптера → към антената. Порт <b>LAN</b> → към този компютър. Адаптерът - в контакта.</li>
  <li>Изчакайте около минута, докато антената тръгне. Програмата я намира сама по кабела (както „Neighbors“ в WinBox) - не пишете IP адреси.</li>
  <li>Ако програмата поиска парола - тя е на етикета на антената (<i>Password</i>). Въвежда се в раздел „Насочване“, най-отгоре.</li>
  <li>Когато горе пише „Антена ✓“, натиснете „Провери антената“. Нищо не се променя - програмата само чете какво има на антената.</li></ol>
  <div id="su-neigh" class="note"></div>
  <label>Антена</label><select id="su-target" style="width:auto"><option value="local">Основна (към която е свързан компютърът)</option><option value="far">Втора (другата страна)</option></select>
  <p><button class="btn pri" id="su-read">Провери антената</button> <span class="note" id="su-msg"></span></p>
  <div id="su-info"></div></div>

 <div class="card"><h3><span class="step">2</span>Коя е тази антена</h3>
  <p>Едната антена е <b>точка за достъп</b>, другата - <b>клиент</b>. Няма значение коя коя е, но обикновено точката за достъп е там, откъдето идва интернетът. Изборът идва от „План и карта“ (кой компютър е А и кой Б) - можете да го смените тук.</p>
  <select id="lk-role" style="width:auto"><option value="ap">А - Точка за достъп</option><option value="client">Б - Клиент</option></select></div>

 <div class="card"><h3><span class="step">3</span>Данни за връзката</h3>
  <p class="note">Попълнено е с подходящи стойности - обикновено трябва само да измислите парола. <b>Името на връзката, паролата и честотата трябва да са еднакви на двете антени.</b></p>
  <div class="grid">
   <div><label>Име на антената</label><input id="lk-ident" value="A-ptp"><div class="note">Вижда се в програмата и в WinBox. Латиница, без интервали.</div></div>
   <div><label>Име на връзката (SSID)</label><input id="lk-ssid" value="PTP-LINK"><div class="note">Еднакво на двете антени.</div></div>
   <div><label>Парола на връзката (8-63 знака)</label><div style="display:flex;gap:6px"><input id="lk-pass" type="password"><button class="btn" id="lk-show">Покажи</button><button class="btn" id="lk-gen">Измисли нова</button></div><div class="note">Латиница и цифри. <b>Еднаква на двете антени - запишете си я.</b> Това не е паролата на admin - тя не се сменя.</div></div>
   <div><label>Честота, MHz</label><div style="display:flex;gap:6px"><input id="lk-freq" type="number" value="5500"><button class="btn" id="lk-fill">От плана</button></div><div class="note">Еднаква на двете антени.</div></div>
   <div><label>Ширина на канала, MHz</label><select id="lk-width"><option>20</option><option>40</option><option>80</option></select><div class="note">Започнете с 20 - по-стабилно е за насочване.</div></div>
   <div><label>IP адрес на антената</label><input id="lk-ip" value="192.168.88.2/24"><div class="note">Различен за двете антени (обикновено .2 и .3). Ако антената ще е в домашна мрежа и искате да я стигате по IP, сложете свободен адрес от нея.</div></div>
   <div><label>Шлюз (по желание)</label><input id="lk-gw" placeholder="празно"><div class="note">Само ако антената трябва да излиза в интернет (за обновяване на RouterOS). Обикновено е адресът на рутера.</div></div>
  </div>
  <details><summary>Допълнителни настройки</summary><div class="grid">
   <div><label>Пакет</label><select id="lk-pkg"><option value="wifi">wifi (RouterOS 7, ax модели)</option><option value="wireless">wireless (класическо меню)</option></select></div>
   <div><label>Безжичен интерфейс</label><input id="lk-if" value="wifi1" list="lk-iflist"><datalist id="lk-iflist"></datalist></div>
   <div><label>Кабелен порт</label><input id="lk-eth" value="ether1"></div>
   <div><label>Обхват</label><select id="lk-band"></select></div>
   <div><label>Държава (латиница)</label><input id="lk-country" value="Bulgaria"></div></div>
   <label><input type="checkbox" id="lk-nv2"> Nv2 (само пакет wireless и MikroTik от двете страни)</label></details>
  <label><input type="checkbox" id="lk-clean"> Изчисти всички стари настройки преди това</label>
  <div class="note">Не е задължително: програмата сама маха фабричните неща, които пречат на моста (раздаване на адреси и др.). Изберете го, ако антената е настройвана преди и искате да започнете на чисто. Паролата на admin остава.</div></div>

 <div class="card"><h3><span class="step">4</span>Преглед и прилагане</h3>
  <ol><li>Натиснете <b>„Покажи какво ще се промени“</b> и прочетете списъка.</li>
  <li>Натиснете <b>„Приложи настройките“</b>. Програмата първо прави резервно копие на сегашните настройки в самата антена, после записва новите.</li>
  <li>Връзката с антената за момент може да прекъсне - това е нормално. Не изключвайте антената, докато не излезе резултат.</li></ol>
  <div class="banner warn"><b>Внимание:</b> промяната на SSID, парола, честота или IP може да прекъсне връзката с антената. Командите са генерирани по документацията на RouterOS и не са проверявани с всички версии - прегледайте ги внимателно.</div>
  <textarea id="lk-out" readonly placeholder="Тук ще се покажат командите..."></textarea>
  <label><input type="checkbox" id="lk-bk" checked> Направи резервно копие преди промяната (препоръчително)</label>
  <label><input type="checkbox" id="lk-ok"> Разбирам, че връзката може да прекъсне</label>
  <p><button class="btn" id="lk-prev">Покажи какво ще се промени</button> <button class="btn pri" id="lk-apply">Приложи настройките</button> <button class="btn" id="lk-backup">Само резервно копие</button> <button class="btn" id="lk-copy">Копирай командите</button></p>
  <div id="lk-res"></div></div>

 <div class="card"><h3><span class="step">5</span>Другата антена и проба на бюрото</h3>
  <ol><li>Свържете другата антена (към този или към другия компютър) и повторете стъпки 1-4: изберете другата роля, <b>същото име на връзката, същата парола и същата честота</b> и друг IP адрес.</li>
  <li>Включете двете антени на бюрото, на 2-3 m една от друга и <b>обърнати встрани</b> - иначе сигналът е твърде силен.</li>
  <li>До минута в раздел „Насочване“ трябва да се появи сигнал. На честоти 5500-5700 MHz (DFS) точката за достъп изчаква 1 минута преди да излъчва (на 5600-5650 MHz - до 10 минути).</li>
  <li>След това пуснете теста на скоростта отдолу - така сте сигурни, че всичко работи, преди да качите антените.</li></ol></div>

 <div class="card"><h3><span class="step">6</span>Тест на скоростта между антените</h3>
  <p>Антената, свързана с този компютър, праща данни към другата и едновременно приема от нея (Bandwidth Test на MikroTik). Пуснете го, когато антените вече се виждат - на бюрото за проба и после на място, след насочването. Интернет и компютрите не участват - мери се само радиовръзката. <b>Тестът запълва връзката докрай</b> - не го пускайте върху работеща мрежа.</p>
  <div id="bt-neigh" class="note"></div>
  <div class="grid">
   <div><label>IP адрес на другата антена</label><input id="bt-addr" placeholder="напр. 192.168.88.3"></div>
   <div><label>Вид на теста</label><select id="bt-proto"><option value="tcp">TCP - като истински трафик</option><option value="udp">UDP - колко най-много може връзката</option></select><div class="note">Започнете с TCP.</div></div>
   <div><label>Посока</label><select id="bt-dir"><option value="both">и двете</option><option value="send">изпращане</option><option value="receive">приемане</option></select></div>
   <div><label>Продължителност</label><select id="bt-dur"><option value="5">5 секунди</option><option value="10" selected>10 секунди</option><option value="20">20 секунди</option><option value="30">30 секунди</option></select></div>
   <div><label>Парола на другата антена (само ако е нужна)</label><input id="bt-pass" type="password" placeholder="не е нужна"><div class="note">Не е нужна, ако двете антени са настроени от програмата с една и съща парола на връзката. Иначе - паролата на admin от етикета ѝ.</div></div>
  </div>
  <p><button class="btn pri" id="bt-go">Пусни теста</button> <button class="btn" id="bt-stop">Спри</button> <span class="note" id="bt-msg"></span></p>
  <div class="two"><div><div class="note">Изпращане сега / средно, Mbps</div><div class="mid" id="bt-tx">--</div></div>
  <div><div class="note">Приемане сега / средно, Mbps</div><div class="mid" id="bt-rx">--</div></div></div>
  <div id="bt-hist" class="note" style="margin-top:8px"></div></div>

 <details><summary>Допълнително: потребител само за четене, мрежа на компютъра, полезни команди</summary><div>
 <div class="card"><h3>Подготви антената за програмата</h3>
  <p class="note">Създава потребител само с права <code>read, api, test</code> (за четене на сигнала и теста на скоростта), включва API услугата и bandwidth-server. Изпълнява се с данните от „Настройки“ (например администраторски).</p>
  <div class="grid">
   <div><label>Нов потребител</label><input id="pr-user" value="alignfree"></div>
   <div><label>Парола (поне 8 знака)</label><div style="display:flex;gap:6px"><input id="pr-pass"><button class="btn" id="pr-gen">Измисли</button></div></div>
   <div><label>Разрешен адрес за API (по желание)</label><input id="pr-allow" placeholder="напр. 192.168.88.0/24"></div></div>
  <label><input type="checkbox" id="pr-bw" checked> Включи bandwidth-server (за теста на скоростта)</label>
  <p><button class="btn" id="pr-prev">Покажи какво ще се промени</button> <button class="btn pri" id="pr-apply">Приложи</button>
  <button class="btn" id="pr-save">Ползвай този потребител в програмата</button></p>
  <textarea id="pr-out" readonly placeholder="Тук ще се покажат командите..."></textarea><div id="pr-res"></div></div>
 <div class="card"><h3>Мрежа на компютъра (Windows)</h3>
  <p class="note">За да говори компютърът с антената, трябва да е в същата мрежа. Тук можете временно да зададете статичен адрес и после да върнете автоматичния. Кабелната карта получава временен IP; бутонът „Върни на автоматичен IP“ го премахва. Windows ще поиска потвърждение (UAC).</p>
  <div class="grid">
   <div><label>Мрежов адаптер</label><div style="display:flex;gap:6px"><input id="nt-name" list="nt-list" placeholder="напр. Ethernet"><datalist id="nt-list"></datalist><button class="btn" id="nt-load">Покажи</button></div></div>
   <div><label>IP адрес на компютъра</label><input id="nt-ip" value="192.168.88.10"></div>
   <div><label>Маска</label><input id="nt-mask" value="255.255.255.0"></div>
   <div><label>Шлюз (по желание)</label><input id="nt-gw"></div></div>
  <p><button class="btn pri" id="nt-set">Настрой компютъра автоматично</button> <button class="btn" id="nt-dhcp">Върни на автоматичен IP</button> <span class="note" id="nt-msg"></span></p></div>
 <div class="card"><h3>Полезни команди за RouterOS (копирайте в терминала на WinBox)</h3><div id="cmds"></div></div>
 </div></details>
</section>

<!-- ====================== ОТЧЕТ ====================== -->
<section id="t-report" class="hide">
 <div class="card"><h3>Протокол от насочването</h3>
  <p class="note">Събира плана, запазените точки и последния тест на скоростта в страница за печат или запис като PDF (печат → „Запази като PDF“).</p>
  <div class="grid">
   <div><label>Клиент</label><input id="rp-client"></div><div><label>Обект / адрес</label><input id="rp-site"></div>
   <div><label>Изпълнител</label><input id="rp-who"></div><div><label>Дата</label><input id="rp-date" type="date"></div>
  </div>
  <label>Бележки</label><textarea id="rp-notes" style="min-height:70px"></textarea>
  <h4>Запазени точки на измерване</h4><div id="pts"></div>
  <p><button class="btn pri" id="rp-print">Отвори за печат</button> <button class="btn" id="rp-dl">Свали като HTML</button>
  <button class="btn" id="rp-csv">Свали записа (CSV)</button> <button class="btn dng" id="pts-clear">Изчисти точките</button></p></div>
</section>

<!-- ====================== ИНСТРУКЦИИ ====================== -->
<section id="t-guide" class="hide">
 <div id="guide-body"></div>
</section>

<!-- ====================== НАСТРОЙКИ ====================== -->
<section id="t-set" class="hide">
 <div class="card"><h3>Антена, към която е свързан този компютър</h3><div class="grid">
  <div><label>Адрес</label><input data-k="router_host"></div>
  <div><label>API порт (8728 или 8729 за SSL)</label><input data-k="router_port" type="number"></div>
  <div><label>Потребител</label><input data-k="router_user"></div>
  <div><label>Парола (празно = без промяна)</label><input data-k="router_password" type="password" autocomplete="new-password"></div>
  <div><label>Само интерфейс (по избор)</label><input data-k="interface" placeholder="напр. wifi1"></div>
  <div><label>Интервал на опресняване, ms</label><input data-k="poll_ms" type="number"></div></div>
  <label><input type="checkbox" data-k="router_ssl"> Използвай API-SSL</label>
  <label><input type="checkbox" data-k="demo"> Демо режим (симулиран сигнал, без антена)</label>
  <p><button class="btn" id="st-test1">Тествай връзката</button> <button class="btn" id="st-scan">Търси антена в мрежата</button> <span class="note" id="st-msg1"></span></p>
  <div id="st-found" class="note"></div></div>

 <div class="card"><h3>Втора антена (другата страна, достъпна през самата връзка)</h3>
  <p class="note">Така един компютър вижда сигнала и от двете страни, без втори лаптоп.</p>
  <label><input type="checkbox" data-k="router2_enabled"> Включи втората антена</label>
  <div class="grid"><div><label>Адрес</label><input data-k="router2_host" placeholder="напр. 192.168.88.2"></div>
  <div><label>API порт</label><input data-k="router2_port" type="number"></div>
  <div><label>Потребител</label><input data-k="router2_user"></div>
  <div><label>Парола (празно = без промяна)</label><input data-k="router2_password" type="password" autocomplete="new-password"></div></div>
  <label><input type="checkbox" data-k="router2_ssl"> Използвай API-SSL</label>
  <p><button class="btn" id="st-test2">Тествай връзката</button> <span class="note" id="st-msg2"></span></p></div>

 <div class="card"><h3>Тест на скоростта (данни за сървъра на отсрещната антена)</h3>
  <div class="grid"><div><label>Потребител (по избор)</label><input data-k="btest_user"></div>
  <div><label>Парола (празно = без промяна)</label><input data-k="btest_password" type="password" autocomplete="new-password"></div></div></div>

 <div class="card"><h3>Синхронизация и разговор между два компютъра</h3>
  <p class="note">Всеки компютър е свързан със своята антена и праща само стойностите на сигнала през публичен MQTT брокер. Стойностите не са криптирани, затова кодът е дълъг и случаен - споделяйте го само с колегата си.</p>
  <label><input type="checkbox" data-k="mqtt_enabled"> Включи синхронизацията</label>
  <div class="grid">
   <div><label>Този компютър е страна</label><select data-k="side"><option value="a">A</option><option value="b">B</option></select></div>
   <div><label>Код на стаята (еднакъв на двата компютъра)</label><input data-k="room"></div>
   <div><label>Брокери (по ред на опити)</label><input data-k="mqtt_brokers"></div></div>
  <p><button class="btn" id="b-room">Нов код</button> <button class="btn" id="b-room-copy">Копирай кода</button>
  <span class="note">След смяна на кода запишете настройките и го въведете на другия компютър.</span></p></div>

 <div class="card"><h3>Външен вид и звук</h3>
  <div class="grid">
   <div><label>Тема</label><select id="st-theme"><option value="auto">Автоматична</option><option value="light">Светла</option><option value="dark">Тъмна</option></select></div>
   <div><label>Говор: на всеки, s</label><input id="st-voice" type="number" min="1" max="30" value="3"></div>
   <div><label>Ниска/висока честота на тона, Hz</label><div style="display:flex;gap:6px"><input id="st-flo" type="number" value="300"><input id="st-fhi" type="number" value="1800"></div></div>
  </div></div>

 <p><button class="btn pri" id="b-save">Запази и свържи</button> <span class="note" id="save-msg"></span></p>
 <div class="card"><h3>Бързи клавиши</h3><p><kbd>Интервал</kbd> звук (изкл./тон/говор) · <kbd>P</kbd> нулира пика · <kbd>S</kbd> запазва точка · <kbd>1</kbd>-<kbd>5</kbd> раздели</p>
  <p class="note">MikrotikAlignFree е безплатна програма, не е официален продукт на MikroTik. Картите са © OpenStreetMap, OpenTopoMap, Esri. Библиотека Leaflet (BSD-2).</p></div>
</section>
</main><footer>MikrotikAlignFree v__VERSION__ · безплатна програма, не е официален продукт на MikroTik</footer>
<div id="tip" class="hide"></div>
<script src="/static/leaflet.js"></script>
<script>
const UILANG=document.documentElement.lang==="bg"?"bg":"en",LOCALE=UILANG==="bg"?"bg-BG":"en-GB",SPEECH=UILANG==="bg"?"bg-BG":"en-US";
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LS={get(k,d){try{const v=localStorage.getItem(k);return v==null?d:JSON.parse(v)}catch(e){return d}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
async function api(path,body){const o=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
 try{const r=await fetch(path,o);return await r.json()}catch(e){return{ok:false,error:'Програмата не отговаря.'}}}
function download(name,text,mime){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:mime||'text/plain'}));a.download=name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},500)}
function copyText(t){if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(t).catch(()=>fallbackCopy(t));fallbackCopy(t);return Promise.resolve()}
function fallbackCopy(t){const e=document.createElement('textarea');e.value=t;document.body.appendChild(e);e.select();try{document.execCommand('copy')}catch(x){}e.remove()}
const f0=x=>x==null||isNaN(x)?'--':Math.round(x);
const randPass=n=>{const a=new Uint8Array(n),c='abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';crypto.getRandomValues(a);return [...a].map(x=>c[x%c.length]).join('')};
function explain(kind,err){const m={timeout:'Антената не отговаря. Проверете адреса, кабела/PoE и дали компютърът е в същата мрежа (напр. 192.168.88.х).',
 refused:'Адресът отговаря, но API услугата е изключена или портът е друг (по подразбиране 8728). В RouterOS: /ip service enable api',
 auth:'Грешно потребителско име или парола (или потребителят няма права read и api).',
 unreachable:'Няма маршрут до този адрес - компютърът е в друга мрежа. Ползвайте „Настройка на антената → Мрежа на компютъра“.',
 ssl:'Грешка при SSL връзката. Опитайте порт 8728 без SSL или проверете сертификата на api-ssl.',
 dns:'Адресът не може да се разреши - въведете IP адрес.',closed:'Връзката е прекъсната от антената.'};
 return m[kind]||err||''}

let PLAN=LS.get('af-plan-result',null),map=null,mk={},lineL=null,beams={},worstM=null,lay={},pick='a',scanTarget=null;
const RPF=['rp-client','rp-site','rp-who','rp-date','rp-notes'],cfgEls=()=>$$('#t-set [data-k]');
const TABS=['aim','plan','setup','report','guide','set'];
/* ---------- раздели ---------- */
function showTab(t){if(!TABS.includes(t))t='aim';
 TABS.forEach(x=>$('#t-'+x).classList.toggle('hide',x!==t));
 $$('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
 $('#toolbar').classList.toggle('hide',t==='set');
 try{history.replaceState(null,'','#'+t)}catch(e){}
 if(t==='plan')setTimeout(initMap,50);if(t==='set')loadCfg();if(t==='guide')renderGuide();if(t==='report')renderReport();
 window.scrollTo(0,0)}
$$('[data-t]').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.t)));
$('#b-help').onclick=()=>showTab('guide');
$('#b-quit').onclick=async()=>{if(confirm('Да спра ли програмата?')){try{await fetch('/api/quit',{method:'POST',body:'{}'})}catch(e){}document.body.innerHTML='<p style="padding:30px">Програмата е спряна. Можете да затворите този раздел.</p>'}};

/* ---------- тема ---------- */
function applyTheme(m){document.documentElement.dataset.theme=m==='auto'?'':m;LS.set('af-theme',m);const s=$('#st-theme');if(s)s.value=m}
$('#b-lang-t').textContent=UILANG==='en'?'BG':'EN';$('#b-lang').onclick=async()=>{try{await api('/api/config',{lang:UILANG==='en'?'bg':'en'})}catch(e){}location.reload()};
$('#b-theme').onclick=()=>{const o=['auto','dark','light'],c=LS.get('af-theme','auto');applyTheme(o[(o.indexOf(c)+1)%3])};
$('#st-theme').onchange=e=>applyTheme(e.target.value);applyTheme(LS.get('af-theme','auto'));

/* ---------- страна, звук, режим ---------- */
let st={},peak=null,soundMode=0,actx=null,mySide=LS.get('af-side','a'),rec=null,lastChat=0,hist=[],lastBt='',peaks={l:null,r:null},aimOn=false,cfgCache={},seenPlan=0,seenVoice='';
function setSide(s){mySide=s;LS.set('af-side',s);$$('#side-seg button').forEach(b=>b.classList.toggle('on',b.dataset.s===s));renderTarget();if(window.syncRole)syncRole()}
$$('#side-seg button').forEach(b=>b.onclick=()=>setSide(b.dataset.s));setSide(mySide);
const local=()=>((st.local&&st.local.peers)||[])[0]||null;
const far=()=>st.far&&st.far.ok?((st.far.peers||[])[0]||null):null;
const mq=()=>{const r=st.mqtt&&st.mqtt.remote;return r&&st.now-r.ts<5?r:null};
function remoteSig(){const f=far();if(f&&f.signal!=null)return{v:f.signal,src:'(втората антена)'};
 const m=mq();if(m&&m.signal!=null)return{v:m.signal,src:'(другия компютър)'};
 const l=local();if(l&&l.tx_signal!=null)return{v:l.tx_signal,src:'(TX сигнал от антената)'};return null}
function cur(){const l=local(),mode=$('#mode').value,mine=l?l.signal:null,r=remoteSig(),rv=r?r.v:null;
 if(mode==='remote')return rv;if(mode==='min'){const a=[mine,rv].filter(x=>x!=null);return a.length?Math.min(...a):null}return mine}
const quality=v=>v>=-60?'var(--g)':v>=-68?'#4d8f12':v>=-75?'var(--y)':v>=-82?'var(--o)':'var(--r)';
const verdictOf=v=>v>=-55?'Отличен сигнал':v>=-65?'Много добър сигнал':v>=-72?'Добър сигнал':v>=-78?'Приемлив сигнал - малък запас':v>=-85?'Слаб сигнал':'Много слаб сигнал';
$('#b-sound').onclick=cycleSound;
function cycleSound(){soundMode=(soundMode+1)%3;$('#b-sound').textContent='Звук: '+['изкл.','тон','говор'][soundMode];
 if(soundMode&&!actx){try{actx=new (window.AudioContext||window.webkitAudioContext)()}catch(e){}}
 if(soundMode===1)beepLoop();if(soundMode===2)voiceLoop()}
let beepT=null,voiceT=null;
function beepLoop(){clearTimeout(beepT);if(soundMode!==1||!actx)return;const v=cur();let next=500;
 if(v!=null){const q=Math.max(0,Math.min(1,(v+90)/50)),lo=+$('#st-flo').value||300,hi=+$('#st-fhi').value||1800;
  const o=actx.createOscillator(),g=actx.createGain();o.frequency.value=lo+q*(hi-lo);g.gain.value=.15;o.connect(g);g.connect(actx.destination);o.start();o.stop(actx.currentTime+.08);next=700-q*600}
 beepT=setTimeout(beepLoop,next)}
function speak(t){try{const u=new SpeechSynthesisUtterance(t);u.lang=SPEECH;u.rate=1.05;speechSynthesis.cancel();speechSynthesis.speak(u)}catch(e){}}
function voiceLoop(){clearTimeout(voiceT);if(soundMode!==2)return;const v=cur();if(v!=null)speak('минус '+Math.abs(v));voiceT=setTimeout(voiceLoop,(+$('#st-voice').value||3)*1000)}
$('#b-peak').onclick=()=>{peak=null;peaks.l=null;peaks.r=null;render()};
$('#mode').onchange=()=>{peak=null;peaks.l=null;peaks.r=null;hist=[];render()};
$('#b-rec').onclick=()=>{if(rec){const n=rec.length;$('#b-rec').textContent='Запис: изкл.';$('#b-rec').classList.remove('pri');
  if(n&&confirm('Записът има '+n+' измервания. Да го сваля ли като CSV?'))downloadCsv()}
 else{rec=[];$('#b-rec').textContent='Запис: вкл.';$('#b-rec').classList.add('pri')}};
function csvText(){return 'време;тук_dBm;другата_страна_dBm;tx_скорост;rx_скорост\n'+(rec||[]).map(r=>[r.t,r.a,r.b,r.tx,r.rx].join(';')).join('\n')}
function downloadCsv(){download('alignfree-zapis-'+new Date().toISOString().slice(0,16).replace(/[:T]/g,'-')+'.csv','﻿'+csvText(),'text/csv')}
$('#b-point').onclick=savePoint;
function savePoint(){const l=local();if(!l){alert('Няма данни за сигнала.');return}const r=remoteSig(),
 label=prompt('Име на точката (напр. „след затягане“):','')||'';const pts=LS.get('af-points',[]);
 pts.push({t:new Date().toLocaleString(LOCALE),side:mySide.toUpperCase(),label:label,here:l.signal,there:r?r.v:null,peak:peak,
  heading:$('#heading').value,tilt:$('#tilt-now').value,tx:l.tx_rate,rx:l.rx_rate});LS.set('af-points',pts);renderPoints()}
const typing=e=>{const t=(e.target.tagName||'').toLowerCase();return t==='input'||t==='textarea'||t==='select'};
document.addEventListener('keydown',e=>{if(typing(e))return;
 if(e.code==='Space'){e.preventDefault();if(!e.repeat)talkStart()}else if(e.key==='m'||e.key==='M')cycleSound();
 else if(e.key==='p'||e.key==='P'){peak=null;peaks.l=null;peaks.r=null;render()}
 else if(e.key==='s'||e.key==='S')savePoint();else if(/^[1-5]$/.test(e.key))showTab(TABS[+e.key-1])});
document.addEventListener('keyup',e=>{if(e.code==='Space'&&!typing(e))talkStop()});

/* ---------- опресняване ---------- */
function pill(id,text,cls){const e=$(id);e.textContent=text;e.className='pill '+cls}
const nv=v=>v==null||v===''||(typeof v==='number'&&isNaN(v))?'—':v;
function ifState(i){if(!i)return '—';return (i.mode||'—')+' / '+(i.disabled==='true'?'изключен':i.running==='true'?'работи':'неактивен')}
function detailRows(p,i,x){
 if(x){return [['Вериги Ch0 / Ch1',nv(x.ch0)+' / '+nv(x.ch1)],['CCQ tx / rx',nv(x.tc)+' / '+nv(x.rc)],['SNR / шум',nv(x.snr)+' / '+nv(x.nf)],
  ['Режим',nv(x.mode)],['Честота / ширина',nv(x.fr)+' / '+nv(x.w)],['Връзка от',nv(x.up)]]}
 return [['Вериги Ch0 / Ch1',nv(p.ch0)+' / '+nv(p.ch1)],['Приема се отсреща',p.tx_signal!=null?p.tx_signal+' dBm':'—'],['CCQ tx / rx',nv(p.tx_ccq)+' / '+nv(p.rx_ccq)],
  ['SNR / шум',nv(p.snr)+' / '+nv(p.noise)],['Скорост tx / rx',nv(p.tx_rate)+' / '+nv(p.rx_rate)],['Режим / състояние',ifState(i)],
  ['Честота / ширина',i?nv(i.frequency)+' / '+nv(i.width):'—'],['Връзка от',nv(p.uptime)]]}
function fillPanel(k,o){
 const sig=o.sig,pk=peaks[k];
 if(sig!=null){peaks[k]=pk==null?sig:Math.max(pk,sig)}
 $('#'+k+'-sig').textContent=sig==null?'--':sig;$('#'+k+'-sig').style.color=sig==null?'':quality(sig);
 $('#'+k+'-dpk').textContent=sig==null||peaks[k]==null?'--':(sig-peaks[k]).toFixed(0);
 $('#'+k+'-bar').style.width=sig==null?'0':Math.max(0,Math.min(100,(sig+90)/50*100))+'%';$('#'+k+'-bar').style.background=sig==null?'':quality(sig);
 $('#'+k+'-exp').textContent=o.exp!=null?'Очакван по плана: '+f0(o.exp)+' dBm'+(sig!=null?' ('+(sig-o.exp>=0?'+':'')+f0(sig-o.exp)+' dB)':''):'';
 const al=$('#'+k+'-alert');if(o.alert){al.textContent=o.alert;al.className='alert'+(o.err?' err':'')}else al.className='alert hide';
 $('#'+k+'-rows').innerHTML=(o.rows||[]).map(r=>'<div class="kv"><span>'+esc(r[0])+'</span><b>'+esc(r[1])+'</b></div>').join('');
 $('#'+k+'-age').textContent=o.age!=null?'данни преди '+o.age+' ms':''}
function render(){
 const L=st.local||{},F=st.far||{},M=st.mqtt||{};
 if(L.ok)pill('#conn','Антена: '+(L.identity||'свързана')+' ✓','ok');else pill('#conn',L.kind==='auth'?'Антена: иска парола':'Антена: няма връзка','bad');
 if(F.enabled)pill('#far-pill',F.ok?'Втора антена: '+(F.identity||'свързана')+' ✓':'Втора антена: няма връзка',F.ok?'ok':'bad');else $('#far-pill').className='pill hide';
 const N=st.internet;if(N&&N.total){pill('#net-pill','Интернет: '+N.ok+'/'+N.total+' сървъра',N.ok===N.total?'ok':N.ok?'warn':'bad')}
 if(M.enabled){const live=!!mq();pill('#mq-pill',M.ok?(live?'Отсреща ✓':'Отсреща: чака...'):'Синхронизация: '+(M.error||'...'),M.ok&&live?'ok':(M.ok?'warn':'bad'))}else $('#mq-pill').className='pill hide';
 renderBanner();
 const aw=st.aim_remote,remoteAim=aw&&aw.on&&st.now-aw.ts<15;$('#aimwarn').classList.toggle('hide',!remoteAim);
 const p=local(),a=mySide==='a';
 const expL=PLAN?(a?PLAN.rxA:PLAN.rxB):null,expR=PLAN?(a?PLAN.rxB:PLAN.rxA):null;
 const li=(L.ifaces||[])[0];
 fillPanel('l',{sig:p?p.signal:null,exp:expL,rows:p?detailRows(p,li):[],age:L.ts?Math.round((st.now-L.ts)*1000):null,
  alert:!L.ok?'Нужно е действие - вижте съобщението над панелите.':(!p?'Антената е свързана, но няма свързан клиент в регистрационната таблица (проверете SSID, парола, честота и роли).':''),err:!L.ok});
 const fp=far(),m=mq();let rsig=null,rows=[],age=null,al='',x=null;
 if(fp){rsig=fp.signal;rows=detailRows(fp,(F.ifaces||[])[0]);age=Math.round((st.now-F.ts)*1000)}
 else if(m){rsig=m.signal;x=m.x||{};rows=[['Приема се отсреща',m.tx_signal!=null?m.tx_signal+' dBm':'—'],['Скорост tx / rx',nv(m.tx_rate)+' / '+nv(m.rx_rate)],['Антена',nv(m.identity)]].concat(detailRows(null,null,x));age=Math.round((st.now-m.ts)*1000)}
 else if(M.enabled)al='Чакам другия компютър... Пуснете програмата там със стая „'+(cfgCache.room||'')+'“.';
 else al='Няма данни отсреща. Включете „Синхронизация“ (втори компютър) или втората антена в „Настройки“.';
 fillPanel('r',{sig:rsig,exp:expR,rows:rows,age:age,alert:al});
 const v=cur();
 if(v!=null){peak=peaks.l;const now=Date.now();hist.push({t:now,v:p?p.signal:null,r:rsig});while(hist.length&&now-hist[0].t>180000)hist.shift();drawHist();
  if(rec&&(!rec.last||now-rec.last>=1000)){rec.last=now;rec.push({t:new Date().toISOString(),a:p?p.signal:'',b:rsig==null?'':rsig,tx:p?p.tx_rate:'',rx:p?p.rx_rate:''})}}
 renderPeers();renderBt();renderChat();renderTurn();renderPlanIn();renderVoice();renderNeigh()}
function renderPeers(){const L=st.local||{},ps=L.peers||[];const e=$('#peers');if(!ps.length){e.innerHTML='<span class="note">Няма свързани устройства в регистрационната таблица.</span>';return}
 e.innerHTML='<table><tr><th>MAC</th><th>Интерфейс</th><th>Сигнал</th><th>Приема се отсреща</th><th>TX</th><th>RX</th><th>CCQ</th><th>Връзка от</th></tr>'+
 ps.map(x=>'<tr><td>'+esc(x.mac)+'</td><td>'+esc(x.interface)+'</td><td>'+f0(x.signal)+'</td><td>'+f0(x.tx_signal)+'</td><td>'+esc(x.tx_rate)+'</td><td>'+esc(x.rx_rate)+'</td><td>'+(x.ccq==null?'--':x.ccq+'%')+'</td><td>'+esc(x.uptime)+'</td></tr>').join('')+'</table>'}
function drawHist(){const cv=$('#hist'),g=cv.getContext('2d'),W=cv.width,H=cv.height,P=24,SPAN=180000;g.clearRect(0,0,W,H);
 const css=getComputedStyle(document.body);g.strokeStyle=css.getPropertyValue('--bd');g.fillStyle=css.color;g.font='11px sans-serif';
 [-90,-80,-70,-60,-50,-40].forEach(v=>{const y=H-P-(H-2*P)*(v+90)/50;g.beginPath();g.moveTo(P+14,y);g.lineTo(W-6,y);g.stroke();g.fillText(v,2,y+4)});
 if(hist.length<2)return;const now=Date.now(),X=t=>W-6-(W-P-20)*(now-t)/SPAN,Y=v=>H-P-(H-2*P)*Math.max(0,Math.min(50,v+90))/50;
 [['v','#1f6feb'],['r','#c2410c']].forEach(s=>{g.strokeStyle=s[1];g.lineWidth=2;g.beginPath();let open=false;hist.forEach(h=>{if(h[s[0]]==null){open=false;return}const x=X(h.t),y=Y(h[s[0]]);if(open)g.lineTo(x,y);else g.moveTo(x,y);open=true});g.stroke()});
 if(peaks.l!=null){g.setLineDash([5,4]);g.lineWidth=1;g.strokeStyle='#1f6feb';g.beginPath();g.moveTo(P+14,Y(peaks.l));g.lineTo(W-6,Y(peaks.l));g.stroke();g.setLineDash([])}}
function renderTarget(){const e=$('#target'),sb=$('#sumbar');
 if(!PLAN){e.innerHTML='<span class="note">Изчислете плана в „План и карта“, за да видите целевия азимут и наклон.</span>';sb.classList.add('hide');return}
 const a=mySide==='a',az=a?PLAN.magA:PLAN.magB,tilt=a?PLAN.tiltA:-PLAN.tiltA,geo=a?PLAN.az:PLAN.azb,rx=a?PLAN.rxA:PLAN.rxB;
 e.innerHTML='<table><tr><td>Страна</td><td><b>'+mySide.toUpperCase()+'</b></td></tr><tr><td>Целеви азимут по компас</td><td><b>'+az.toFixed(1)+'°</b> (географски '+geo.toFixed(1)+'°)</td></tr>'+
 '<tr><td>Целеви наклон</td><td><b>'+tiltTxt(tilt)+'</b></td></tr><tr><td>Очакван сигнал</td><td>'+f0(rx)+' dBm</td></tr></table>';
 sb.classList.remove('hide');
 sb.innerHTML='<b style="color:'+(a?'#1f6feb':'#c2410c')+'">'+mySide.toUpperCase()+' → '+(a?'Б':'А')+'</b> <span class="note">'+(PLAN.D/1000).toFixed(2)+' km</span> <span class="note">Азимут</span> <b>'+geo.toFixed(1)+'°</b> <span class="note">По компас</span> <b>'+az.toFixed(1)+'°</b> <span class="note">Наклон</span> <b>'+tiltTxt(tilt)+'</b> <span class="note">Очакван сигнал</span> <b>'+f0(rx)+' dBm</b>'}
function renderTurn(){const e=$('#turn');if(!PLAN){e.textContent='';return}const a=mySide==='a',az=a?PLAN.magA:PLAN.magB,tilt=a?PLAN.tiltA:-PLAN.tiltA,out=[];
 const h=parseFloat($('#heading').value);if(!isNaN(h)){const d=((az-h+540)%360)-180;out.push(Math.abs(d)<1?'Посоката съвпада (отклонение '+d.toFixed(1)+'°).':'Завъртете '+Math.abs(d).toFixed(1)+'° '+(d>0?'надясно (по часовниковата стрелка)':'наляво')+'.')}
 const t=parseFloat($('#tilt-now').value);if(!isNaN(t)){const d=tilt-t;out.push(Math.abs(d)<0.3?'Наклонът съвпада.':'Наклонете '+Math.abs(d).toFixed(1)+'° '+(d>0?'нагоре':'надолу')+'.')}
 e.textContent=out.join(' ')}
$('#heading').oninput=renderTurn;$('#tilt-now').oninput=renderTurn;

/* ---------- банер за връзка, откриване на антени ---------- */
let pickedLogin=false;
function renderBanner(){const L=st.local||{},cb=$('#cb'),nb=st.neighbors||[],host=cfgCache.router_host;
 const show=!L.ok&&L.ts;cb.classList.toggle('hide',!show);if(!show){pickedLogin=false;return}
 const me=nb.find(n=>n.ip===host),auth=L.kind==='auth';
 let t='';
 if(auth)t='<b>'+(me?'Намерих '+esc(me.identity||'антена')+(me.board?' ('+esc(me.board)+')':'')+', но иска парола.':'Антената иска парола.')+'</b> Записаната парола не става. Паролата е на етикета на антената (или тази, която сте сложили).';
 else t='<b>Няма връзка с антената ('+esc(host||'?')+').</b> '+esc(explain(L.kind,L.error))+(L.error?' <span class="note">(техническо: '+esc(L.error)+')</span>':'');
 $('#cb-main').innerHTML=t;
 $('#cb-login').style.display=auth||pickedLogin?'flex':'none';$('#cb-login').classList.remove('hide');
 $('#cb-steps').classList.toggle('hide',auth);
 const nbh=nb.length?'<span class="note">Виждам '+nb.length+' '+(nb.length===1?'антена':'антени')+' - изберете тази, която е <b>с кабел към този компютър</b>: </span>'+nb.map(n=>'<button class="btn'+(n.ip===host?' pri':'')+'" data-nip="'+esc(n.ip)+'">'+esc(n.identity||'MikroTik')+(n.board?' ('+esc(n.board)+')':'')+' · '+esc(n.ip)+(n.same_net?'':' · друга мрежа')+'</button>').join(' '):'<span class="note">Не виждам антени по кабела (откриване MNDP). Изчакайте минута след включване или опитайте „Търси по IP адреси“.</span>';
 $('#cb-neigh').innerHTML=nbh;
 $$('#cb-neigh [data-nip]').forEach(b=>b.onclick=async()=>{pickedLogin=true;await api('/api/config',{router_host:b.dataset.nip});await loadCfgCache();render()});
 const other=nb.find(n=>n.ip===host&&!n.same_net)||nb.find(n=>!n.same_net&&!nb.some(m=>m.same_net));
 $('#cb-auto').classList.toggle('hide',!(other));}
async function loadCfgCache(){cfgCache=await api('/api/config');roomChip(cfgCache)}
$('#cb-go').onclick=async()=>{await api('/api/config',{router_user:$('#cb-user').value,router_password:$('#cb-pass').value});$('#cb-main').innerHTML='<b>Свързвам...</b>';await loadCfgCache()};
$('#cb-pass').onkeydown=e=>{if(e.key==='Enter')$('#cb-go').click()};
$('#cb-auto').onclick=async()=>{const nb=(st.neighbors||[]).find(n=>!n.same_net);if(!nb)return;
 const ad=await api('/api/netsh/adapters');if(!ad.ok){alert(ad.error);return}
 let name=ad.adapters.find(x=>/ethernet|lan|кабел/i.test(x))||ad.adapters[0];if(ad.adapters.length>1)name=prompt('Кой адаптер е с кабела към антената?\n'+ad.adapters.join('\n'),name)||'';if(!name)return;
 const parts=nb.ip.split('.');let last=250;if(+parts[3]===last)last=249;const ip=parts[0]+'.'+parts[1]+'.'+parts[2]+'.'+last;
 if(!confirm('Ще задам на адаптер „'+name+'“ временен адрес '+ip+', за да стигне до антената. Windows ще поиска потвърждение. Продължавам ли?'))return;
 const j=await api('/api/netsh/set',{name:name,ip:ip,mask:'255.255.255.0',gateway:'',dhcp:false});$('#ob-found').textContent=j.ok?'Готово. Адресът е зададен - след няколко секунди връзката трябва да тръгне. „Върни на автоматичен IP“ е в „Настройка на антената → Допълнително“.':j.error;};
$('#ob-set').onclick=()=>showTab('set');$('#ob-guide').onclick=()=>showTab('guide');
$('#ob-demo').onclick=async()=>{await api('/api/config',{demo:true});$('#ob-found').textContent='Демо режимът е включен.'};
$('#ob-scan').onclick=()=>startScan('#ob-found');

/* ---------- „Аз насочвам“ ---------- */
function setAim(on){aimOn=on;$('#b-aim').classList.toggle('pri',on);$('#b-aim').textContent=on?'Аз насочвам ✓':'Аз насочвам';api('/api/aim',{on:on})}
$('#b-aim').onclick=()=>setAim(!aimOn);
setInterval(()=>{if(aimOn)api('/api/aim',{on:true})},5000);

/* ---------- разговор, глас ---------- */
const QUICK=['Чуваш ли ме?','Чакай','Върти бавно','Расте ↑','Пада ↓','Върни назад','Спри - пикът е тук','Готово - твой ред'];
$('#quick').innerHTML=QUICK.map(q=>'<button class="btn">'+esc(q)+'</button>').join('');
$$('#quick button').forEach((b,i)=>b.onclick=()=>sendChat(QUICK[i]));
async function sendChat(t){t=(t||'').trim();if(!t)return;const j=await api('/api/chat',{text:t});if(!j.ok)alert(j.error);else $('#chat-text').value=''}
$('#chat-send').onclick=()=>sendChat($('#chat-text').value);$('#chat-text').onkeydown=e=>{if(e.key==='Enter')sendChat(e.target.value)};
function renderChat(){const c=st.chat,M=st.mqtt||{};$('#chat-state').textContent=M.enabled?(M.ok&&mq()?'другият компютър е на линия':'другият компютър не е на линия'):'(изисква синхронизация - Настройки)';
 if(!c||!c.log||!c.log.length)return;
 $('#chatlog').innerHTML=c.log.slice(-25).map(m=>'<div class="'+(m.mine?'me':'')+'"><b>'+(m.mine?'Аз':'Страна '+esc(String(m.side).toUpperCase()))+':</b> '+esc(m.text)+(m.mine?(m.delivered?' <span class="ok">✓</span>':' <span class="note">…</span>'):'')+'</div>').join('');
 $('#chatlog').scrollTop=1e9;
 c.log.forEach(m=>{if(m.id>lastChat){if(lastChat>0&&!m.mine){if($('#chat-speak').checked)speak(m.text);beepOnce()}lastChat=m.id}})}
function beepOnce(){try{actx=actx||new (window.AudioContext||window.webkitAudioContext)();const o=actx.createOscillator(),g=actx.createGain();o.frequency.value=880;g.gain.value=.2;o.connect(g);g.connect(actx.destination);o.start();o.stop(actx.currentTime+.15)}catch(e){}}
let mrec=null,mchunks=[],talking=false,talkTimer=null;
async function talkStart(){if(talking)return;talking=true;
 try{const s=await navigator.mediaDevices.getUserMedia({audio:true});if(!talking){s.getTracks().forEach(t=>t.stop());return}
  const mime=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'].find(m=>window.MediaRecorder&&MediaRecorder.isTypeSupported(m))||'';
  mrec=new MediaRecorder(s,mime?{mimeType:mime,audioBitsPerSecond:24000}:{audioBitsPerSecond:24000});mchunks=[];
  mrec.ondataavailable=e=>{if(e.data&&e.data.size)mchunks.push(e.data)};
  mrec.onstop=()=>{s.getTracks().forEach(t=>t.stop());const blob=new Blob(mchunks,{type:mrec.mimeType||'audio/webm'});
   if(blob.size<600)return;if(blob.size>110000){alert('Записът е твърде дълъг. Говорете по-кратко (до 15 секунди).');return}
   const fr=new FileReader();fr.onload=async()=>{const j=await api('/api/voice',{m:blob.type,d:String(fr.result).split(',')[1]});if(!j.ok)alert(j.error)};fr.readAsDataURL(blob)};
  mrec.start();$('#b-talk').classList.add('pri');clearTimeout(talkTimer);talkTimer=setTimeout(talkStop,15000)}
 catch(e){talking=false;alert('Микрофонът не е достъпен: '+e.message+'. Разрешете го в браузъра (иконата вляво от адреса).')}}
function talkStop(){talking=false;clearTimeout(talkTimer);if(mrec&&mrec.state==='recording')mrec.stop();$('#b-talk').classList.remove('pri')}
$('#b-talk').addEventListener('pointerdown',e=>{e.preventDefault();talkStart()});
['pointerup','pointerleave','pointercancel'].forEach(ev=>$('#b-talk').addEventListener(ev,talkStop));
async function renderVoice(){const v=st.voice_in;if(!v||v.id===seenVoice)return;seenVoice=v.id;
 const j=await api('/api/voice?id='+v.id);if(!j.ok)return;try{const a=new Audio('data:'+j.m+';base64,'+j.d);a.play().catch(()=>{});
}catch(e){}}
/* ---------- получен план ---------- */
function renderPlanIn(){const pi=st.plan_in,e=$('#planin');if(!pi||pi.ts===seenPlan){e.classList.add('hide');return}
 e.classList.remove('hide');e.innerHTML='<b>Другата страна ('+esc(String(pi.side).toUpperCase())+') изпрати план.</b> <button class="btn pri" id="pi-ok">Приложи и изчисли</button> <button class="btn" id="pi-no">Отхвърли</button>';
 $('#pi-no').onclick=()=>{seenPlan=pi.ts;e.classList.add('hide')};
 $('#pi-ok').onclick=async()=>{seenPlan=pi.ts;e.classList.add('hide');const j=await api('/api/plan_in');if(j.ok){applyInputs(j.inputs);savePlanInputs();showTab('plan');setTimeout(calc,300)}}}

/* ---------- тест на скоростта ---------- */
$('#bt-go').onclick=async()=>{const body={address:$('#bt-addr').value.trim(),protocol:$('#bt-proto').value,direction:$('#bt-dir').value,duration:+$('#bt-dur').value||10,password:$('#bt-pass').value};
 LS.set('af-bt-addr',body.address);const j=await api('/api/btest',body);$('#bt-msg').textContent=j.ok?'Стартиране...':j.error};
$('#bt-stop').onclick=()=>api('/api/btest/stop',{});
$('#bt-addr').value=LS.get('af-bt-addr','');
const f1=x=>x==null?'--':x.toFixed(0);
function renderBt(){const b=st.btest||{};if(!b.state||b.state==='idle')return;
 $('#bt-tx').textContent=f1(b.tx_now)+' / '+f1(b.tx_avg);$('#bt-rx').textContent=f1(b.rx_now)+' / '+f1(b.rx_avg);
 const m={running:'Тестът върви...',done:'Готово.',stopped:'Спрян.',error:'Грешка: '+(b.error||'')};$('#bt-msg').textContent=m[b.state]||'';
 if(b.state==='done'&&b.started&&String(b.started)!==lastBt){lastBt=String(b.started);const h=LS.get('af-bt-hist',[]);
  h.unshift({t:new Date().toLocaleString(LOCALE),addr:b.address,proto:b.protocol,dir:b.direction,tx:b.tx_avg,rx:b.rx_avg});LS.set('af-bt-hist',h.slice(0,10));renderBtHist()}}
function renderBtHist(){const h=LS.get('af-bt-hist',[]);$('#bt-hist').innerHTML=h.length?'<b>Последни тестове:</b><br>'+h.map(x=>esc(x.t)+' - '+esc(x.addr)+' '+esc(x.proto)+'/'+esc(x.dir)+': изпращане '+f1(x.tx)+', приемане '+f1(x.rx)+' Mbps').join('<br>'):''}
renderBtHist();
async function poll(){try{const r=await fetch('/api/state');st=await r.json();render();renderScan()}catch(e){pill('#conn','Програмата не отговаря','bad')}setTimeout(poll,400)}

/* ---------- търсене на антена ---------- */

async function startScan(sel){scanTarget=sel;$(sel).textContent='Търся в мрежата...';const j=await api('/api/scan',{});if(!j.ok)$(sel).textContent=j.error}
function renderScan(){const s=st.scan;if(!s||!scanTarget)return;const el=$(scanTarget);if(!el)return;
 if(s.state==='running'){el.textContent='Търся в мрежите '+(s.nets||[]).map(n=>n+'.х').join(', ')+' ... '+s.done+'/'+s.total+(s.found.length?' - намерени: '+s.found.join(', '):'');return}
 if(s.state==='done'){if(!s.found.length){el.innerHTML='Не е намерена антена с отворен API порт (8728). Мрежи на компютъра: '+esc((s.mine||[]).join(', ')||'не са открити')+'. Ако компютърът не е в мрежата на антената, настройте го от „Настройка на антената → Мрежа на компютъра“.'}
  else el.innerHTML='Намерени: '+s.found.map(ip=>'<button class="btn" data-ip="'+esc(ip)+'" data-w="1">'+esc(ip)+' - основна</button> <button class="btn" data-ip="'+esc(ip)+'" data-w="2">втора</button>').join(' ');
  el.querySelectorAll('button[data-ip]').forEach(b=>b.onclick=async()=>{const k=b.dataset.w==='2'?'router2':'router',body={};body[k+'_host']=b.dataset.ip;if(k==='router2')body.router2_enabled=true;
   await api('/api/config',body);el.textContent='Адресът '+b.dataset.ip+' е записан. Въведете потребител и парола в „Настройки“.';if($('#t-set').classList.contains('hide')===false)loadCfg()});
  scanTarget=null}}
$('#st-scan').onclick=()=>startScan('#st-found');

/* ---------- настройки ---------- */

function roomChip(c){const e=$('#room-chip');if(c&&c.room){e.textContent='стая: '+c.room;e.className='pill'}else e.className='pill hide'}
async function loadCfg(){const c=await api('/api/config');roomChip(c);
 cfgEls().forEach(e=>{const k=e.dataset.k;if(e.type==='checkbox')e.checked=!!c[k];else if(k.endsWith('password')){e.value='';e.placeholder=c['has_'+k]?'(запазена)':''}else e.value=c[k]==null?'':c[k]});
 if(!$('#bt-addr').value&&c.router2_host)$('#bt-addr').value=c.router2_host}
$('#b-save').onclick=async()=>{const body={};
 cfgEls().forEach(e=>{const k=e.dataset.k;body[k]=e.type==='checkbox'?e.checked:e.type==='number'?+e.value:e.value});
 LS.set('af-snd',{v:$('#st-voice').value,lo:$('#st-flo').value,hi:$('#st-fhi').value});
 const j=await api('/api/config',body);$('#save-msg').textContent=j.ok?'Запазено.':'Грешка при запис.';await loadCfg();cfgCache=await api('/api/config');roomChip(cfgCache)};
const snd=LS.get('af-snd',null);if(snd){$('#st-voice').value=snd.v;$('#st-flo').value=snd.lo;$('#st-fhi').value=snd.hi}
$('#b-room').onclick=()=>{document.querySelector('[data-k=room]').value=randPass(14)};
$('#b-room-copy').onclick=()=>copyText(document.querySelector('[data-k=room]').value);
async function testRouter(which,msg){const pre=which==='router2'?'router2_':'router_',g=k=>document.querySelector('[data-k='+pre+k+']'),el=$(msg);
 el.textContent='Проверявам...';const j=await api('/api/test_router',{which:which,host:g('host').value.trim(),port:+g('port').value,user:g('user').value,password:g('password').value,ssl:g('ssl').checked});
 el.innerHTML=j.ok?'<span class="ok">✓ Свързване успешно: '+esc(j.identity)+' · '+esc(j.board)+' · RouterOS '+esc(j.version)+'</span>':'<span class="badc">✗ '+esc(explain(j.kind,j.error))+'</span> <span class="note">('+esc(j.error)+')</span>'}
$('#st-test1').onclick=()=>testRouter('router','#st-msg1');$('#st-test2').onclick=()=>testRouter('router2','#st-msg2');

/* ---------- данни за моделите ---------- */
const DEVICES=[
{n:'Друго - въведете усилването',g:null,grp:'Друго'},
{n:'SXTsq 5 ac / ax / Lite5 - 16 dBi',g:16,grp:'Насочени антени (точка-точка, CPE)'},
{n:'LHG 5 / LHG 5 ac / HP5 - 24.5 dBi',g:24.5,grp:'Насочени антени (точка-точка, CPE)'},
{n:'LHG XL 5 / XL HP5 - 27 dBi',g:27,grp:'Насочени антени (точка-точка, CPE)'},
{n:'QRT 5 / QRT 5 ac - 24 dBi',g:24,grp:'Насочени антени (точка-точка, CPE)'},
{n:'Disc Lite5 / Disc Lite5 ac - 21 dBi',g:21,grp:'Насочени антени (точка-точка, CPE)'},
{n:'DynaDish 5 - 25 dBi',g:25,grp:'Насочени антени (точка-точка, CPE)'},
{n:'SXTsq Lite2 (2.4 GHz) - 10 dBi',g:10,grp:'Насочени антени (точка-точка, CPE)'},
{n:'LHG 2 (2.4 GHz) - 18 dBi',g:18,grp:'Насочени антени (точка-точка, CPE)'},
{n:'mANTBox 15s - 15 dBi, сектор 120°',g:15,bw:120,grp:'Сектори и точки за достъп (AP)'},
{n:'mANTBox 19s - 19 dBi, сектор 120°',g:19,bw:120,grp:'Сектори и точки за достъп (AP)'},
{n:'mANTBox 52 15s - 15 dBi, сектор 120°',g:15,bw:120,grp:'Сектори и точки за достъп (AP)'},
{n:'Сектор 90° - 17 dBi (друг)',g:17,bw:90,grp:'Сектори и точки за достъп (AP)'},
{n:'Сектор 120° - 15 dBi (друг)',g:15,bw:120,grp:'Сектори и точки за достъп (AP)'},
{n:'Всепосочна антена - 8 dBi (външна)',g:8,bw:360,grp:'Сектори и точки за достъп (AP)'},
{n:'cAP ax, 5 GHz - ~5.5 dBi',g:5.5,bw:360,grp:'Вътрешни и малки AP (всепосочни)'},
{n:'cAP ax, 2.4 GHz - ~4 dBi',g:4,bw:360,grp:'Вътрешни и малки AP (всепосочни)'},
{n:'wAP ac - ~2.5 dBi',g:2.5,bw:360,grp:'Вътрешни и малки AP (всепосочни)'}];
['a','b'].forEach(s=>{const sel=$('#'+s+'-dev');let h='',g=null;DEVICES.forEach((d,i)=>{if(d.grp!==g){if(g!==null)h+='</optgroup>';h+='<optgroup label="'+esc(d.grp)+'">';g=d.grp}h+='<option value="'+i+'">'+esc(d.n)+'</option>'});sel.innerHTML=h+'</optgroup>';sel.value='1';
 sel.onchange=()=>{const d=DEVICES[+sel.value];if(d.g!=null)$('#'+s+'-gain').value=d.g;$('#'+s+'-beam').placeholder=d.bw?d.bw+' (от модела)':'авто'}});
const devBw=s=>{const d=DEVICES[+$('#'+s+'-dev').value];return d&&d.bw?d.bw:null};
const MCS=[[6.5,-94],[13,-92],[19.5,-90],[26,-87],[39,-83],[52,-79],[58.5,-77],[65,-75],[78,-71],[86.7,-69]];
function speedEst(rx,w){const pen=10*Math.log10(w/20),sc={20:1,40:2.077,80:4.5}[w]||1;let best=-1;MCS.forEach((m,i)=>{if(rx>=m[1]+pen+6)best=i});
 return best<0?null:{mcs:best,phy:MCS[best][0]*sc*2,real:MCS[best][0]*sc*2*0.6}}
function regInfo(f,eirp){let lim=null,t='';
 if(f>=2400&&f<=2483.5){lim=20;t='2.4 GHz: до 20 dBm EIRP (ЕС).'}
 else if(f>=5150&&f<5250){lim=23;t='5150-5250 MHz: до 23 dBm EIRP, само на закрито.'}
 else if(f>=5250&&f<=5350){lim=23;t='5250-5350 MHz: до 23 dBm EIRP, DFS и TPC.'}
 else if(f>=5470&&f<=5725){lim=30;t='5470-5725 MHz: до 30 dBm EIRP, DFS и TPC (ЕС).'}
 else if(f>=5725&&f<=5875){lim=14;t='5725-5875 MHz: до 14 dBm EIRP (SRD) - проверете местните правила.'}
 const dfs=(f>=5250&&f<=5725);let d='';if(dfs){d='Честотата е с DFS: точката за достъп изчаква 1 минута преди да излъчва'+(f>=5590&&f<=5660?' (в диапазона 5600-5650 MHz - 10 минути)':'')+'.'}
 return{t:t,d:d,over:lim!=null&&eirp>lim,lim:lim}}

/* ---------- план на връзката ---------- */
const R=6371000,rad=x=>x*Math.PI/180,deg=x=>x*180/Math.PI;
function parseLL(s){const m=String(s).match(/(-?\d{1,3}\.\d+)\s*[,; ]\s*(-?\d{1,3}\.\d+)/);return m?{lat:+m[1],lon:+m[2]}:null}
function dist(a,b){const dl=rad(b.lat-a.lat),dn=rad(b.lon-a.lon),h=Math.sin(dl/2)**2+Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dn/2)**2;return 2*R*Math.asin(Math.sqrt(h))}
function bearing(a,b){const y=Math.sin(rad(b.lon-a.lon))*Math.cos(rad(b.lat)),x=Math.cos(rad(a.lat))*Math.sin(rad(b.lat))-Math.sin(rad(a.lat))*Math.cos(rad(b.lat))*Math.cos(rad(b.lon-a.lon));return (deg(Math.atan2(y,x))+360)%360}
function lerp(a,b,t){return{lat:a.lat+(b.lat-a.lat)*t,lon:a.lon+(b.lon-a.lon)*t}}
function dest(p,az,dm){const d=dm/R,a=rad(az),l1=rad(p.lat),n1=rad(p.lon),l2=Math.asin(Math.sin(l1)*Math.cos(d)+Math.cos(l1)*Math.sin(d)*Math.cos(a)),n2=n1+Math.atan2(Math.sin(a)*Math.sin(d)*Math.cos(l1),Math.cos(d)-Math.sin(l1)*Math.sin(l2));return{lat:deg(l2),lon:((deg(n2)+540)%360)-180}}
const beamFor=g=>Math.min(120,Math.max(3,Math.sqrt(27000/Math.pow(10,g/10))));
const V=id=>+$('#'+id).value;
async function elevations(pts){const u='https://api.open-meteo.com/v1/elevation?latitude='+pts.map(p=>p.lat.toFixed(5)).join(',')+'&longitude='+pts.map(p=>p.lon.toFixed(5)).join(',');
 const r=await fetch(u);if(!r.ok)throw new Error('Open-Meteo: '+r.status);const j=await r.json();return j.elevation}
const SENS={0:-94,7:-77,9:-71};
$('#b-calc').onclick=calc;$('#b-relief').onclick=calc;
$('#plan-send').onclick=async()=>{savePlanInputs();const j=await api('/api/plan/send',{inputs:snapInputs()});$('#plan-send-msg').textContent=j.ok?'Изпратено.':j.error};
async function calc(){
 const A=parseLL($('#a-ll').value),B=parseLL($('#b-ll').value),msg=$('#calc-msg');
 if(!A||!B){msg.textContent='Невалидни координати. Пример: 41.8485, 23.1117';return}
 if(A.lat===B.lat&&A.lon===B.lon){msg.textContent='Точките съвпадат.';return}
 savePlanInputs();msg.textContent='';
 if($('#decl-auto').checked){const mid=lerp(A,B,.5);const j=await api('/api/declination?lat='+mid.lat+'&lon='+mid.lon);
  if(j&&j.declination!=null){$('#decl').value=j.declination;if(!j.valid)msg.textContent='Моделът WMM2025 е валиден до 2030 г. - проверете отклонението. '}else msg.textContent='Отклонението не може да се изчисли. '}
 const D=dist(A,B),N=80,pts=[];for(let i=0;i<N;i++)pts.push(lerp(A,B,i/(N-1)));
 let el,flat=false;try{el=await elevations(pts)}catch(e){msg.textContent+='Релефът не е наличен ('+e.message+') - профилът е без релеф. ';el=pts.map(()=>0);flat=true}
 const auto0=el[0],autoN=el[N-1];$('#a-elev').placeholder=Math.round(auto0)+' (авто)';$('#b-elev').placeholder=Math.round(autoN)+' (авто)';
 if($('#a-elev').value!=='')el[0]=+$('#a-elev').value;if($('#b-elev').value!=='')el[N-1]=+$('#b-elev').value;
 const f=V('freq')/1000,k=V('kf')||1.33,cl=V('clutter'),Dkm=D/1000,hA=el[0]+V('a-mast'),hB=el[N-1]+V('b-mast');
 let worst={r:1e9,i:0,d:0};const prof=[];
 for(let i=0;i<N;i++){const d=D*i/(N-1),d1=d/1000,d2=Dkm-d1,bulge=d*(D-d)/(2*k*R),ends=(i<3||i>N-4),terr=el[i]+bulge+(ends?0:cl),
  los=hA+(hB-hA)*i/(N-1),F=17.32*Math.sqrt(Math.max(d1*d2,0)/(f*Dkm)),clr=los-terr;
  prof.push({d,terr,base:el[i]+bulge,los,F,clr,lat:pts[i].lat,lon:pts[i].lon,el:el[i]});if(F>0.5&&clr/F<worst.r)worst={r:clr/F,i:i,d:d}}
 const az=bearing(A,B),azb=bearing(B,A),dc=V('decl'),mag=a=>(a-dc+360)%360,tiltA=deg(Math.atan((hB-hA)/D));
 const fspl=32.44+20*Math.log10(V('freq'))+20*Math.log10(Dkm),loss=V('loss'),gA=V('a-gain'),gB=V('b-gain');
 const rxB=V('a-tx')+gA+gB-fspl-loss,rxA=V('b-tx')+gA+gB-fspl-loss,w=V('width'),pen=10*Math.log10(w/20);
 PLAN={A:A,B:B,D:D,az:az,azb:azb,decl:dc,magA:mag(az),magB:mag(azb),tiltA:tiltA,fspl:fspl,rxA:rxA,rxB:rxB,worstR:worst.r,worstD:worst.d,worstAt:prof[worst.i],
  freq:V('freq'),width:w,hA:hA,hB:hB,mastA:V('a-mast'),mastB:V('b-mast'),flat:flat,prof:prof,at:new Date().toLocaleString(LOCALE),
  marginA:{m0:rxA-(SENS[0]+pen),m7:rxA-(SENS[7]+pen)},marginB:{m0:rxB-(SENS[0]+pen),m7:rxB-(SENS[7]+pen)},beamA:+$('#a-beam').value||devBw('a')||beamFor(gA),beamB:+$('#b-beam').value||devBw('b')||beamFor(gB)};
 PLAN.nameA=$('#a-name').value||'A';PLAN.nameB=$('#b-name').value||'B';PLAN.gA=gA;PLAN.gB=gB;PLAN.eirpA=V('a-tx')+gA;PLAN.eirpB=V('b-tx')+gB;PLAN.k=k;
 PLAN.fMid=Math.max.apply(null,prof.map(x=>x.F));PLAN.bMid=(D/2)*(D/2)/(2*k*R);PLAN.spA=speedEst(rxA,w);PLAN.spB=speedEst(rxB,w);PLAN.elA=el[0];PLAN.elB=el[N-1];PLAN.clutter=cl;
 LS.set('af-plan-result',Object.assign({},PLAN,{prof:[]}));renderPlan();drawProfile();mapUpdate();renderTarget();renderGuide()}
function verdictPlan(r){return r>=1?['чисто','var(--g)']:r>=0.6?['приемливо (над 60% от зоната на Френел)','var(--y)']:r>0?['частично затъмнение на зоната на Френел','var(--o)']:['няма пряка видимост','var(--r)']}
const tiltTxt=a=>a>=0?a.toFixed(2)+'° нагоре':(-a).toFixed(2)+'° надолу';
function renderPlan(){const P=PLAN;if(!P)return;const v=verdictPlan(P.worstR),pct=Math.round(P.worstR*100),reg=regInfo(P.freq,Math.max(P.eirpA,P.eirpB));
 const sp=s=>s?'MCS'+s.mcs+', до ≈'+Math.round(s.phy)+' Mbps по канала (реално ≈'+Math.round(s.real)+' Mbps)':'под прага на MCS0 - връзката няма да се вдигне';
 const acc=bw=>bw>=90?'лъчът е широк - точната посока е без значение':'насочете с точност ±'+(bw*0.289).toFixed(1)+'° (под 1 dB загуба)';
 const side=(L,nm,col,az,mag,tilt,el,mast,rx,bw,gain,tx)=>'<div class="card" style="border-top:3px solid '+col+';margin:0"><h3>'+L+' '+(L==='A'?'→ '+esc(P.nameB):'→ '+esc(P.nameA))+' <span class="note">('+esc(nm)+')</span></h3>'+
  '<div class="mid">'+az.toFixed(1)+'°</div><div class="note">азимут спрямо географския север</div>'+
  '<table><tr><td>По компас</td><td><b>'+mag.toFixed(1)+'°</b> <span class="note">(декл. '+P.decl.toFixed(1)+'° '+(P.decl>=0?'изток':'запад')+')</span></td></tr>'+
  '<tr><td>Наклон</td><td><b>'+tiltTxt(tilt)+'</b></td></tr>'+
  '<tr><td>Антена</td><td>'+Math.round(el)+' m терен + '+mast.toFixed(1)+' m = <b>'+Math.round(el+mast)+' m н.в.</b></td></tr>'+
  '<tr><td>Очакван сигнал тук</td><td><b style="color:var(--o)">'+f0(rx)+' dBm</b> <span class="note">±3 dB</span></td></tr>'+
  '<tr><td>Ширина на лъча</td><td>≈'+Math.round(bw)+'° → '+acc(bw)+'</td></tr></table></div>';
 $('#res').innerHTML='<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(250px,1fr))">'+
 '<div class="card" style="margin:0"><h3>Трасе</h3><div class="mid">'+(P.D/1000).toFixed(2)+' km</div>'+
 '<table><tr><td>Видимост</td><td><b style="color:'+v[1]+'">'+v[0]+' ('+pct+'%)</b></td></tr>'+
 '<tr><td>Зона на Френел</td><td>радиус '+P.fMid.toFixed(1)+' m в средата; 60% = '+(P.fMid*0.6).toFixed(1)+' m</td></tr>'+
 '<tr><td>Кривина на Земята</td><td>'+P.bMid.toFixed(1)+' m в средата (k='+P.k+')</td></tr>'+
 (P.clutter?'<tr><td>Дървета/сгради</td><td>'+P.clutter+' m по трасето</td></tr>':'')+'</table>'+
 '<p class="note">Най-тясното място е на '+Math.round(P.worstD)+' m от A: свободни са '+pct+'% от зоната на Френел (нужни са поне 60%).'+(P.flat?' <b class="badc">Профилът е без релеф - видимостта не е надеждна.</b>':'')+'</p></div>'+
 side('A',P.nameA,'#1f6feb',P.az,P.magA,P.tiltA,P.elA,P.mastA,P.rxA,P.beamA,P.gA)+
 side('B',P.nameB,'#c2410c',P.azb,P.magB,-P.tiltA,P.elB,P.mastB,P.rxB,P.beamB,P.gB)+
 '<div class="card" style="margin:0"><h3>Сигнал и скорост</h3><table>'+
 '<tr><td>Загуба в пространството</td><td>'+P.fspl.toFixed(1)+' dB при '+P.freq+' MHz</td></tr>'+
 '<tr><td>Очакван при A / при B</td><td><b style="color:var(--o)">'+f0(P.rxA)+' / '+f0(P.rxB)+' dBm</b> <span class="note">±3 dB</span></td></tr>'+
 '<tr><td>EIRP</td><td>A: '+P.eirpA.toFixed(1)+' dBm · B: '+P.eirpB.toFixed(1)+' dBm</td></tr>'+
 '<tr><td>Скорост при A</td><td>'+sp(P.spA)+'</td></tr><tr><td>Скорост при B</td><td>'+sp(P.spB)+'</td></tr></table>'+
 (reg.t?'<p class="note'+(reg.over?'" style="color:var(--r)':'')+'">'+esc(reg.t)+(reg.over?' <b>EIRP надвишава ограничението ('+reg.lim+' dBm) - намалете мощността.</b>':'')+'</p>':'')+
 (reg.d?'<p class="note">'+esc(reg.d)+'</p>':'')+
 '<p class="note">Ориентировъчно: при правилно насочване измереният сигнал обикновено е в рамките на ±3-5 dB от очаквания. Скоростта е груба оценка за 2 потока (2x2) със запас 6 dB.</p></div></div>'}
function drawProfile(){if(!PLAN||!PLAN.prof.length)return;const prof=PLAN.prof,D=PLAN.D,cv=$('#cv'),g=cv.getContext('2d'),W=cv.width,H=cv.height,P=34;g.clearRect(0,0,W,H);
 let lo=1e9,hi=-1e9;prof.forEach(p=>{lo=Math.min(lo,p.terr);hi=Math.max(hi,p.terr,p.los+p.F)});const span=Math.max(hi-lo,10);lo-=span*.05;hi+=span*.05;
 const X=d=>P+(W-2*P)*d/D,Y=h=>H-P-(H-2*P)*(h-lo)/(hi-lo),css=getComputedStyle(document.body);
 g.fillStyle='rgba(31,111,235,.13)';g.beginPath();prof.forEach((p,i)=>{const y=Y(p.los+p.F);i?g.lineTo(X(p.d),y):g.moveTo(X(p.d),y)});for(let i=prof.length-1;i>=0;i--)g.lineTo(X(prof[i].d),Y(prof[i].los-prof[i].F));g.closePath();g.fill();
 g.fillStyle='#cdb892';g.beginPath();g.moveTo(X(0),H-P);prof.forEach(p=>g.lineTo(X(p.d),Y(p.base!=null?p.base:p.terr)));g.lineTo(X(D),H-P);g.closePath();g.fill();
 if(PLAN.clutter>0){g.fillStyle='rgba(59,154,74,.75)';g.beginPath();prof.forEach((p,i)=>{const y=Y(p.terr);i?g.lineTo(X(p.d),y):g.moveTo(X(p.d),y)});for(let i=prof.length-1;i>=0;i--)g.lineTo(X(prof[i].d),Y(prof[i].base));g.closePath();g.fill()}
 g.strokeStyle='#1f6feb';g.lineWidth=2;g.beginPath();prof.forEach((p,i)=>i?g.lineTo(X(p.d),Y(p.los)):g.moveTo(X(p.d),Y(p.los)));g.stroke();
 g.setLineDash([6,4]);g.lineWidth=1;g.strokeStyle='#c2410c';[-0.6,0.6].forEach(s=>{g.beginPath();prof.forEach((p,i)=>{const y=Y(p.los+s*p.F);i?g.lineTo(X(p.d),y):g.moveTo(X(p.d),y)});g.stroke()});g.setLineDash([]);
 g.fillStyle=css.color;g.font='13px sans-serif';g.fillText(PLAN.nameA||'A',X(0)+4,16);g.fillText(PLAN.nameB||'B',X(D)-24,16);
 [[0,prof[0].los,'#1f6feb'],[D,prof[prof.length-1].los,'#c2410c']].forEach(m=>{g.fillStyle=m[2];g.beginPath();g.arc(X(m[0]),Y(m[1]),5,0,7);g.fill()});g.fillStyle=css.color;
 for(let k=1;k<D/1000;k++){const x=X(k*1000);g.fillText(k+' km',x-14,H-P+14);g.strokeStyle=css.getPropertyValue('--bd');g.beginPath();g.moveTo(x,H-P);g.lineTo(x,H-P+4);g.stroke()}g.fillText(Math.round(lo)+' m',4,H-P+14);g.fillText(Math.round(hi)+' m',4,P);g.fillText((D/1000).toFixed(2)+' km',W/2-20,H-8);
 cv._geo={X,Y,P,W,D,prof}}
$('#cv').addEventListener('mousemove',e=>{const cv=$('#cv'),G=cv._geo;if(!G)return;const r=cv.getBoundingClientRect(),x=(e.clientX-r.left)*cv.width/r.width,d=(x-G.P)/(G.W-2*G.P)*G.D;
 if(d<0||d>G.D){$('#tip').classList.add('hide');return}let b=G.prof[0];G.prof.forEach(p=>{if(Math.abs(p.d-d)<Math.abs(b.d-d))b=p});
 const t=$('#tip');t.classList.remove('hide');t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY+12)+'px';
 t.textContent=(b.d/1000).toFixed(2)+' km · терен '+Math.round(b.el)+' m · линия '+Math.round(b.los)+' m · свободно '+Math.round(b.clr/Math.max(b.F,.1)*100)+'% от зоната'});
$('#cv').addEventListener('mouseleave',()=>$('#tip').classList.add('hide'));

/* ---------- карта ---------- */

const pinIcon=(l,c)=>L.divIcon({className:'',html:'<div class="pin" style="background:'+c+'"><span>'+l+'</span></div>',iconSize:[28,28],iconAnchor:[14,28]});
function initMap(){if(!window.L){$('#map').innerHTML='<div class="note" style="padding:20px">Библиотеката за карта не се зареди.</div>';return}
 if(map){map.invalidateSize();return}
 try{$('#map').innerHTML='';map=L.map('map').setView([42.7,25.3],7);
  lay.osm=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'});
  lay.topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{maxZoom:17,subdomains:'abc',attribution:'© OpenStreetMap, SRTM | © OpenTopoMap (CC-BY-SA)'});
  lay.sat=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,attribution:'Imagery © Esri'});
  lay.cur=lay.osm;lay.osm.addTo(map);L.control.scale({metric:true,imperial:false}).addTo(map);
  map.on('click',e=>setPoint(pick,e.latlng.lat,e.latlng.lng));
  mapMarkers(true)}catch(e){$('#map').innerHTML='<div class="note" style="padding:20px">Картата не може да се покаже: '+esc(e.message)+'</div>'}}
function mapMarkers(fit){if(!map)return;const pts={a:parseLL($('#a-ll').value),b:parseLL($('#b-ll').value)},col={a:'#1f6feb',b:'#c2410c'};
 ['a','b'].forEach(s=>{const p=pts[s];if(!p){if(mk[s]){map.removeLayer(mk[s]);mk[s]=null}return}
  if(!mk[s]){mk[s]=L.marker([p.lat,p.lon],{draggable:true,icon:pinIcon(s.toUpperCase(),col[s])}).addTo(map);mk[s].on('dragend',()=>{const q=mk[s].getLatLng();setPoint(s,q.lat,q.lng,true)})}else mk[s].setLatLng([p.lat,p.lon])});
 if(lineL){map.removeLayer(lineL);lineL=null}if(pts.a&&pts.b)lineL=L.polyline([[pts.a.lat,pts.a.lon],[pts.b.lat,pts.b.lon]],{color:'#555',weight:2,dashArray:'6 6'}).addTo(map).bindTooltip((dist(pts.a,pts.b)/1000).toFixed(2)+' km',{permanent:true,direction:'center'});
 if(fit&&pts.a&&pts.b)map.fitBounds([[pts.a.lat,pts.a.lon],[pts.b.lat,pts.b.lon]],{padding:[40,40]})}
function mapUpdate(){if(!map)return;mapMarkers(false);const P=PLAN;if(!P)return;
 ['a','b'].forEach(s=>{if(beams[s]){map.removeLayer(beams[s]);beams[s]=null}});
 const mk2=(p,az,beam,c)=>{const pts=[[p.lat,p.lon]];for(let i=0;i<=12;i++){const q=dest(p,az-beam/2+beam*i/12,Math.min(P.D*0.6,3000));pts.push([q.lat,q.lon])}return L.polygon(pts,{color:c,weight:1,fillOpacity:.18}).addTo(map)};
 beams.a=mk2(P.A,P.az,P.beamA,'#1f6feb');beams.b=mk2(P.B,P.azb,P.beamB,'#c2410c');
 if(worstM){map.removeLayer(worstM);worstM=null}if(P.worstAt)worstM=L.circleMarker([P.worstAt.lat,P.worstAt.lon],{radius:7,color:'#c42b2b',fillOpacity:.6}).addTo(map).bindTooltip('Най-тясно място ('+Math.round(P.worstR*100)+'% свободно)')}
function setPoint(w,lat,lon,fromDrag){$('#'+w+'-ll').value=lat.toFixed(6)+', '+lon.toFixed(6);if(!fromDrag)mapMarkers(false);else mapMarkers(false)}
function selectPick(s){pick=s;$$('#pick-seg button').forEach(x=>x.classList.toggle('on',x.dataset.p===s))}
$$('#pick-seg button').forEach(b=>b.onclick=()=>selectPick(b.dataset.p));
$$('[data-pickbtn]').forEach(b=>b.onclick=()=>{selectPick(b.dataset.pickbtn);$('#map').scrollIntoView({behavior:'smooth',block:'center'});$('#map-note').innerHTML='<b>Щракнете върху картата, за да зададете точка '+b.dataset.pickbtn.toUpperCase()+'.</b>'});
$$('[data-herebtn]').forEach(b=>b.onclick=()=>{selectPick(b.dataset.herebtn);$('#b-loc').click()});
$$('#lay-seg button').forEach(b=>b.onclick=()=>{if(!map)return;map.removeLayer(lay.cur);lay.cur=lay[b.dataset.l];lay.cur.addTo(map);$$('#lay-seg button').forEach(x=>x.classList.toggle('on',x===b))});
$('#b-fit').onclick=()=>mapMarkers(true);
$('#b-loc').onclick=()=>{if(!navigator.geolocation){alert('Устройството не поддържа определяне на местоположение.');return}
 navigator.geolocation.getCurrentPosition(p=>{setPoint(pick,p.coords.latitude,p.coords.longitude);if(map)map.setView([p.coords.latitude,p.coords.longitude],15)},e=>alert('Местоположението не е достъпно: '+e.message),{enableHighAccuracy:true,timeout:15000})};
['a-ll','b-ll'].forEach(id=>$('#'+id).addEventListener('change',()=>mapMarkers(true)));

/* ---------- запазване на въведеното и проекти ---------- */
const PLAN_IDS=['a-ll','a-mast','a-dev','a-gain','a-tx','a-beam','b-ll','b-mast','b-dev','b-gain','b-tx','b-beam','freq','width','loss','clutter','kf','decl','decl-auto','a-name','a-elev','b-name','b-elev'];
const snapInputs=()=>PLAN_IDS.map(i=>{const e=$('#'+i);return e.type==='checkbox'?e.checked:e.value});
function applyInputs(a){PLAN_IDS.forEach((i,n)=>{const e=$('#'+i);if(a[n]==null)return;if(e.type==='checkbox')e.checked=!!a[n];else e.value=a[n]});mapMarkers(true)}
function savePlanInputs(){LS.set('af-plan-inputs',snapInputs())}
const si=LS.get('af-plan-inputs',null);if(si)applyInputs(si);
function renderProjects(sel){const pr=LS.get('af-projects',{});$('#proj-list').innerHTML=Object.keys(pr).map(n=>'<option>'+esc(n)+'</option>').join('');if(sel)$('#proj-list').value=sel}
$('#proj-save').onclick=()=>{const n=$('#proj-name').value.trim();if(!n){alert('Въведете име на проекта.');return}const pr=LS.get('af-projects',{});pr[n]=snapInputs();LS.set('af-projects',pr);renderProjects(n)};
$('#proj-load').onclick=()=>{const pr=LS.get('af-projects',{}),n=$('#proj-list').value;if(pr[n]){applyInputs(pr[n]);$('#proj-name').value=n}};
$('#proj-del').onclick=()=>{const pr=LS.get('af-projects',{}),n=$('#proj-list').value;if(n&&confirm('Да изтрия ли „'+n+'“?')){delete pr[n];LS.set('af-projects',pr);renderProjects()}};
$('#proj-exp').onclick=()=>download('alignfree-proekti.json',JSON.stringify({v:1,ids:PLAN_IDS,projects:LS.get('af-projects',{})},null,1),'application/json');
$('#proj-imp').onchange=e=>{const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=()=>{try{const j=JSON.parse(r.result),pr=LS.get('af-projects',{});Object.assign(pr,j.projects||{});LS.set('af-projects',pr);renderProjects()}catch(x){alert('Невалиден файл.')}};r.readAsText(f);e.target.value=''};
renderProjects();

/* ---------- настройка на антената ---------- */
function bandOptions(){const wifi=$('#lk-pkg').value==='wifi',o=wifi?['5ghz-ax','5ghz-ac','5ghz-n','2ghz-ax','2ghz-n']:['5ghz-a/n/ac','5ghz-onlyn','5ghz-n/ac','2ghz-b/g/n','2ghz-onlyn'];
 $('#lk-band').innerHTML=o.map(x=>'<option>'+esc(x)+'</option>').join('');$('#lk-if').value=wifi?'wifi1':'wlan1'}
$('#lk-pkg').onchange=bandOptions;bandOptions();
$('#su-read').onclick=async()=>{$('#su-msg').textContent='Чета...';const j=await api('/api/router_info?target='+$('#su-target').value);
 if(!j.ok){$('#su-msg').innerHTML='<span class="badc">✗ '+esc(explain(j.kind,j.error))+'</span>';$('#su-info').innerHTML='';return}
 $('#su-msg').textContent='';const i=j.info;
 $('#su-info').innerHTML='<table><tr><td>Име</td><td><b>'+esc(i.identity)+'</b></td><td>Платка</td><td>'+esc(i.board)+' '+esc(i.model)+'</td></tr><tr><td>RouterOS</td><td>'+esc(i.version)+'</td><td>Време на работа / процесор</td><td>'+esc(i.uptime)+' / '+esc(i.cpu)+'%</td></tr></table>'+
 '<h4>Безжични интерфейси ('+esc(i.pkg||'не са намерени')+')</h4><table><tr><th>Име</th><th>Режим</th><th>SSID</th><th>Честота</th><th>Ширина</th><th>Обхват</th><th>Състояние</th></tr>'+
 i.interfaces.map(x=>'<tr><td>'+esc(x.name)+'</td><td>'+esc(x.mode)+'</td><td>'+esc(x.ssid)+'</td><td>'+esc(x.frequency)+'</td><td>'+esc(x.width)+'</td><td>'+esc(x.band)+'</td><td>'+(x.disabled==='true'?'изключен':x.running==='true'?'работи':'неактивен')+'</td></tr>').join('')+'</table>'+
 '<h4>IP адреси</h4>'+(i.addresses.length?i.addresses.map(a=>esc(a.address)+' <span class="note">('+esc(a.interface)+')</span>').join('<br>'):'<span class="note">няма</span>');
 if(i.pkg){$('#lk-pkg').value=i.pkg;bandOptions()}if(i.interfaces.length){$('#lk-if').value=i.interfaces[0].name;$('#lk-iflist').innerHTML=i.interfaces.map(x=>'<option value="'+esc(x.name)+'">').join('')}}
$('#pr-gen').onclick=()=>{$('#pr-pass').value=randPass(14)};$('#lk-gen').onclick=()=>{$('#lk-pass').value=randPass(14)};
const prParams=()=>({user:$('#pr-user').value.trim(),password:$('#pr-pass').value,allow:$('#pr-allow').value.trim(),bwserver:$('#pr-bw').checked});
const lkParams=()=>({pkg:$('#lk-pkg').value,role:$('#lk-role').value,iface:$('#lk-if').value.trim(),ssid:$('#lk-ssid').value,passphrase:$('#lk-pass').value,band:$('#lk-band').value,frequency:$('#lk-freq').value,width:$('#lk-width').value,country:$('#lk-country').value.trim(),nv2:$('#lk-nv2').checked,identity:$('#lk-ident').value.trim(),ip:$('#lk-ip').value.trim(),gateway:$('#lk-gw').value.trim(),eth:$('#lk-eth').value.trim(),clean:$('#lk-clean').checked});
$('#lk-show').onclick=()=>{const i=$('#lk-pass');i.type=i.type==='password'?'text':'password';$('#lk-show').textContent=i.type==='password'?'Покажи':'Скрий'};
function syncRole(){const ap=mySide==='a';if($('#lk-role').dataset.touched!=='1')$('#lk-role').value=ap?'ap':'client';roleDefaults()}
function roleDefaults(){const ap=$('#lk-role').value==='ap';if(/^[AB]-ptp$/.test($('#lk-ident').value))$('#lk-ident').value=ap?'A-ptp':'B-ptp';if(/^192\.168\.88\.[23]\/24$/.test($('#lk-ip').value))$('#lk-ip').value=ap?'192.168.88.2/24':'192.168.88.3/24'}
$('#lk-role').onchange=()=>{$('#lk-role').dataset.touched='1';roleDefaults()};
async function preview(kind,params,out){const j=await api('/api/setup/preview',{kind:kind,params:params});$(out).value=j.ok?j.commands.join('\n'):'Грешка: '+j.error;return j.ok}
$('#pr-prev').onclick=()=>preview('prepare',prParams(),'#pr-out');$('#lk-prev').onclick=()=>preview('link',lkParams(),'#lk-out');
function showResults(sel,j){$(sel).innerHTML=j.results?j.results.map(r=>'<div class="'+(r.ok?'ok':'badc')+'">'+(r.ok?'✓ ':'✗ ')+esc(r.cli)+(r.error?' - '+esc(r.error):'')+(r.note?' <span class="note">('+esc(r.note)+')</span>':'')+'</div>').join('')+(j.ok?'<div class="ok"><b>Готово.</b> Ако връзката е прекъснала, изчакайте минута и проверете в „Насочване“ (при смяна на IP - изберете антената отново от списъка).</div>':''):'<div class="badc">✗ '+esc(explain(j.kind,j.error))+'</div>'}
$('#pr-apply').onclick=async()=>{if(!await preview('prepare',prParams(),'#pr-out'))return;if(!confirm('Да изпълня ли тези команди върху '+($('#su-target').value==='far'?'втората':'основната')+' антена?'))return;
 const j=await api('/api/setup/apply',{kind:'prepare',params:prParams(),confirm:true,target:$('#su-target').value});showResults('#pr-res',j)};
$('#pr-save').onclick=async()=>{const p=prParams(),far=$('#su-target').value==='far',b={};b[(far?'router2':'router')+'_user']=p.user;b[(far?'router2':'router')+'_password']=p.password;
 const j=await api('/api/config',b);$('#pr-res').innerHTML=j.ok?'<div class="ok">✓ Новият потребител е записан в настройките на програмата.</div>':'<div class="badc">✗ Грешка при запис.</div>'};
$('#lk-fill').onclick=()=>{$('#lk-freq').value=$('#freq').value;$('#lk-width').value=$('#width').value};
$('#lk-copy').onclick=()=>copyText($('#lk-out').value);
$('#lk-backup').onclick=async()=>{const j=await api('/api/setup/backup',{target:$('#su-target').value});
 $('#lk-res').innerHTML=j.ok?'<div class="ok">✓ Копието е записано в антената: '+esc(j.name)+'.backup'+(j.export?' и '+esc(j.name)+'.rsc':'')+' (Files в Winbox).</div>':'<div class="badc">✗ '+esc(explain(j.kind,j.error))+'</div>'};
$('#lk-apply').onclick=async()=>{if(!$('#lk-ok').checked){alert('Отбележете потвърждението под командите.');return}if(!await preview('link',lkParams(),'#lk-out'))return;
 if(!confirm('Промяната може да прекъсне връзката. Да продължа ли?'))return;
 const j=await api('/api/setup/apply',{kind:'link',params:lkParams(),confirm:true,backup:$('#lk-bk').checked,target:$('#su-target').value});showResults('#lk-res',j)};
$('#nt-load').onclick=async()=>{const j=await api('/api/netsh/adapters');if(!j.ok){$('#nt-msg').textContent=j.error;return}$('#nt-list').innerHTML=j.adapters.map(a=>'<option value="'+esc(a)+'">').join('');$('#nt-msg').textContent='Намерени: '+j.adapters.join(', ')};
async function ntSet(dhcp){const j=await api('/api/netsh/set',{name:$('#nt-name').value.trim(),ip:$('#nt-ip').value.trim(),mask:$('#nt-mask').value.trim(),gateway:$('#nt-gw').value.trim(),dhcp:dhcp});
 $('#nt-msg').innerHTML=j.ok?'<span class="ok">✓ Готово ('+esc(j.command)+')</span>':'<span class="badc">✗ '+esc(j.error)+'</span>'}
$('#nt-set').onclick=()=>{if($('#nt-name').value.trim()&&confirm('Да сменя ли IP адреса на този компютър? Windows ще поиска потвърждение.'))ntSet(false)};
$('#nt-dhcp').onclick=()=>{if($('#nt-name').value.trim()&&confirm('Да върна ли автоматичния IP адрес?'))ntSet(true)};
const CMDS=[['Включване на API услугата','/ip service set api disabled=no'],['Име на устройството','/system identity set name=AP1-HOME'],
 ['IP адрес на управление (сменете интерфейса)','/ip address add address=192.168.88.1/24 interface=bridge'],['Bandwidth-server (за теста на скоростта)','/tool bandwidth-server set enabled=yes authenticate=yes'],
 ['Показване на свързаните устройства (wifi)','/interface wifi registration-table print'],['Показване на свързаните устройства (wireless)','/interface wireless registration-table print'],
 ['Резервно копие','/system backup save name=pred-promyana'],['Изключване на ненужни услуги','/ip service disable telnet,ftp,www']];
$('#cmds').innerHTML=CMDS.map((c,i)=>'<div style="margin:6px 0"><div class="note">'+esc(c[0])+'</div><div style="display:flex;gap:6px"><code style="flex:1;padding:6px">'+esc(c[1])+'</code><button class="btn" data-c="'+i+'">Копирай</button></div></div>').join('');
$$('#cmds button').forEach(b=>b.onclick=()=>copyText(CMDS[+b.dataset.c][1]));

/* ---------- отчет ---------- */

function renderPoints(){const p=LS.get('af-points',[]);$('#pts').innerHTML=p.length?'<table><tr><th>Време</th><th>Страна</th><th>Име</th><th>Тук, dBm</th><th>Другата, dBm</th><th>Пик</th><th>Азимут</th><th>Наклон</th></tr>'+
 p.map(x=>'<tr><td>'+esc(x.t)+'</td><td>'+esc(x.side)+'</td><td>'+esc(x.label)+'</td><td>'+f0(x.here)+'</td><td>'+f0(x.there)+'</td><td>'+f0(x.peak)+'</td><td>'+esc(x.heading)+'</td><td>'+esc(x.tilt)+'</td></tr>').join('')+'</table>':'<span class="note">Още няма запазени точки. Използвайте „Запази точка“ при насочване.</span>'}
function renderReport(){const r=LS.get('af-report',{});RPF.forEach(id=>{if(r[id]!=null)$('#'+id).value=r[id]});if(!$('#rp-date').value)$('#rp-date').value=new Date().toISOString().slice(0,10);renderPoints()}
RPF.forEach(id=>$('#'+id).addEventListener('input',()=>{const r={};RPF.forEach(k=>r[k]=$('#'+k).value);LS.set('af-report',r)}));
$('#pts-clear').onclick=()=>{if(confirm('Да изчистя ли запазените точки?')){LS.set('af-points',[]);renderPoints()}};
$('#rp-csv').onclick=()=>{if(!rec||!rec.length){alert('Няма запис. Включете „Запис“ в лентата и насочвайте.');return}downloadCsv()};
function reportHtml(){const g=id=>esc($('#'+id).value),P=PLAN,pts=LS.get('af-points',[]),bt=LS.get('af-bt-hist',[])[0];
 let h='<!doctype html><html lang="'+document.documentElement.lang+'"><head><meta charset="utf-8"><title>Протокол - '+g('rp-client')+'</title><style>body{font:14px/1.5 Arial,sans-serif;margin:24px;color:#111}h1{font-size:20px}h2{font-size:16px;margin-top:22px;border-bottom:1px solid #999}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:4px 8px;text-align:left}.sig{margin-top:50px;display:flex;gap:60px}.sig div{border-top:1px solid #000;padding-top:4px;width:220px}</style></head><body>';
 h+='<h1>Протокол от насочване на безжична връзка</h1><table><tr><td>Клиент</td><td>'+g('rp-client')+'</td><td>Дата</td><td>'+g('rp-date')+'</td></tr><tr><td>Обект</td><td>'+g('rp-site')+'</td><td>Изпълнител</td><td>'+g('rp-who')+'</td></tr></table>';
 if(P){h+='<h2>План на връзката</h2><table><tr><th></th><th>A</th><th>B</th></tr><tr><td>Координати</td><td>'+P.A.lat.toFixed(6)+', '+P.A.lon.toFixed(6)+'</td><td>'+P.B.lat.toFixed(6)+', '+P.B.lon.toFixed(6)+'</td></tr><tr><td>Височина на антената</td><td>'+P.mastA+' m</td><td>'+P.mastB+' m</td></tr><tr><td>Азимут по компас</td><td>'+P.magA.toFixed(1)+'°</td><td>'+P.magB.toFixed(1)+'°</td></tr><tr><td>Наклон</td><td>'+tiltTxt(P.tiltA)+'</td><td>'+tiltTxt(-P.tiltA)+'</td></tr><tr><td>Очакван сигнал</td><td>'+f0(P.rxA)+' dBm</td><td>'+f0(P.rxB)+' dBm</td></tr></table><p>Разстояние '+(P.D/1000).toFixed(2)+' km, честота '+P.freq+' MHz, канал '+P.width+' MHz, видимост: '+verdictPlan(P.worstR)[0]+' ('+Math.round(P.worstR*100)+'%).</p>'}
 if(pts.length){h+='<h2>Измервания</h2><table><tr><th>Време</th><th>Страна</th><th>Име</th><th>Тук, dBm</th><th>Другата, dBm</th><th>Пик</th></tr>'+pts.map(x=>'<tr><td>'+esc(x.t)+'</td><td>'+esc(x.side)+'</td><td>'+esc(x.label)+'</td><td>'+f0(x.here)+'</td><td>'+f0(x.there)+'</td><td>'+f0(x.peak)+'</td></tr>').join('')+'</table>'}
 if(bt)h+='<h2>Тест на скоростта</h2><p>'+esc(bt.t)+' - '+esc(bt.addr)+' ('+esc(bt.proto)+', '+esc(bt.dir)+'): изпращане '+f1(bt.tx)+' Mbps, приемане '+f1(bt.rx)+' Mbps.</p>';
 if($('#rp-notes').value.trim())h+='<h2>Бележки</h2><p>'+g('rp-notes').replace(/\n/g,'<br>')+'</p>';
 h+='<div class="sig"><div>Изпълнител</div><div>Клиент</div></div><p style="color:#777;font-size:12px;margin-top:30px">Създадено с AlignFree. Очакваните стойности са теоретични.</p></body></html>';return h}
$('#rp-print').onclick=()=>{const w=window.open('','_blank');if(!w){alert('Браузърът блокира новия прозорец. Разрешете го или свалете HTML.');return}w.document.write(reportHtml());w.document.close();setTimeout(()=>w.print(),300)};
$('#rp-dl').onclick=()=>download('protokol-'+($('#rp-date').value||'')+'.html',reportHtml(),'text/html');
renderPoints();

/* ---------- инструкции ---------- */
const GC=[];
function cmdBox(title,text){GC.push(text);return '<div class="note" style="margin-top:8px"><b>'+esc(title)+'</b> <button class="btn" data-gc="'+(GC.length-1)+'">Копирай</button></div><pre class="cmd">'+esc(text)+'</pre>'}
function linkCmds(role,pkg,P){const f=P?P.freq:5500,ap=role==='ap',n=ap?'A-ptp':'B-ptp',ip=ap?'192.168.88.2':'192.168.88.3',pw='CHANGE-THIS-PASSWORD';
 const head='/system identity set name='+n+'\n/interface bridge add name=bridge1\n/interface bridge port add bridge=bridge1 interface=ether1\n';
 if(pkg==='wifi')return head+'/interface bridge port add bridge=bridge1 interface=wifi1\n/ip address add address='+ip+'/24 interface=bridge1\n/interface wifi set wifi1 configuration.mode='+(ap?'ap':'station-bridge')+' configuration.ssid=PTP-LINK configuration.country=Bulgaria configuration.installation=outdoor channel.band=5ghz-ax channel.frequency='+f+' channel.width=20mhz security.authentication-types=wpa2-psk security.passphrase="'+pw+'" disabled=no\n/ip service enable api\n/user add name=align group=read password=CHANGE-THIS-TOO';
 return head+'/interface bridge port add bridge=bridge1 interface=wlan1\n/ip address add address='+ip+'/24 interface=bridge1\n/interface wireless security-profiles add name=ptp mode=dynamic-keys authentication-types=wpa2-psk wpa2-pre-shared-key="'+pw+'"\n/interface wireless set wlan1 mode='+(ap?'bridge':'station-bridge')+' ssid=PTP-LINK band=5ghz-a/n/ac frequency='+f+' channel-width=20mhz country=bulgaria installation=outdoor security-profile=ptp wireless-protocol=802.11 '+(ap?'':'scan-list='+f+' ')+'disabled=no\n/ip service enable api\n/user add name=align group=read password=CHANGE-THIS-TOO'}
function renderNeigh(){const nb=st.neighbors||[],e=$('#su-neigh');if(!e)return;e.innerHTML=nb.length?'Антени по кабела: '+nb.map(n=>esc(n.identity||'MikroTik')+(n.board?' ('+esc(n.board)+')':'')+' · '+esc(n.ip)).join('; '):'Още не виждам антени по кабела.';
 const bn=$('#bt-neigh');if(bn){const host=cfgCache.router_host,o=nb.filter(n=>n.ip!==host);bn.innerHTML=o.length?'Намерена: '+o.map(n=>'<button class="btn" data-bt="'+esc(n.ip)+'">'+esc(n.identity||'MikroTik')+(n.board?' ('+esc(n.board)+')':'')+' · '+esc(n.ip)+'</button>').join(' '):'';$$('#bt-neigh [data-bt]').forEach(b=>b.onclick=()=>{$('#bt-addr').value=b.dataset.bt})}}
function renderGuide(){GC.length=0;const P=PLAN,e=$('#guide-body');if(!e)return;
 let h='';
 if(!P)h+='<div class="card"><p class="note">Изчислете плана в „План и карта“ - тук ще се покажат вашите числа (азимут по компас, наклон, очакван сигнал), а инструкциите ще ги ползват.</p></div>';
 else{const v=verdictPlan(P.worstR);h+='<div class="card"><h3>Вашите числа <span class="note">(изчислено '+esc(P.at)+')</span></h3><table><tr><th></th><th>A '+esc(P.nameA)+'</th><th>Б '+esc(P.nameB)+'</th></tr>'+
 '<tr><td>Координати</td><td>'+P.A.lat.toFixed(6)+', '+P.A.lon.toFixed(6)+'</td><td>'+P.B.lat.toFixed(6)+', '+P.B.lon.toFixed(6)+'</td></tr>'+
 '<tr><td>Антена над терена</td><td>'+P.mastA.toFixed(1)+' m</td><td>'+P.mastB.toFixed(1)+' m</td></tr><tr><td>Азимут (географски)</td><td>'+P.az.toFixed(1)+'°</td><td>'+P.azb.toFixed(1)+'°</td></tr>'+
 '<tr><td><b>По компас</b></td><td><b>'+P.magA.toFixed(1)+'°</b></td><td><b>'+P.magB.toFixed(1)+'°</b></td></tr><tr><td>Наклон</td><td>'+tiltTxt(P.tiltA)+'</td><td>'+tiltTxt(-P.tiltA)+'</td></tr>'+
 '<tr><td>Очакван сигнал</td><td>'+f0(P.rxA)+' dBm</td><td>'+f0(P.rxB)+' dBm</td></tr></table>'+
 '<p>Разстояние <b>'+(P.D/1000).toFixed(2)+' km</b>. Видимост: <b style="color:'+v[1]+'">'+v[0]+' ('+Math.round(P.worstR*100)+'%)</b>. Най-тясното място е на '+Math.round(P.worstD)+' m от A: свободни са '+Math.round(P.worstR*100)+'% от зоната на Френел (нужни са поне 60%).</p></div>'}
 const az=s=>P?(s==='a'?P.magA:P.magB).toFixed(1)+'°':'(изчислете плана)',tl=s=>P?tiltTxt(s==='a'?P.tiltA:-P.tiltA):'(изчислете плана)',bw=P?Math.round(Math.min(P.beamA,P.beamB)):'';
 const sec=(t,b,open)=>'<details'+(open?' open':'')+'><summary>'+t+'</summary><div>'+b+'</div></details>';
 h+=sec('1. Как работи връзката','<ul><li>Двете антени и предават, и приемат - няма „приемник“ и „предавател“. Едната е <b>точка за достъп</b> (режим <code>ap</code>; в по-старото меню Wireless се казва <code>bridge</code>), другата е <b>клиент</b> (режим <code>station-bridge</code>).</li><li>Точката за достъп обикновено е при източника на интернет. В тази програма това е точка A, а клиентът е Б (можете да ги размените).</li><li>Връзката е прозрачен мост: каквото влезе в кабела от едната страна, излиза от другата. Рутерът и DHCP остават както са си.</li><li>Сигналът (dBm) е отрицателно число - <b>по-близо до нула е по-добре</b>. Всяка страна измерва сигнала, с който чува другата, и за добра връзка трябва да е добър от <b>двете</b> страни.</li></ul>',true);
 h+=sec('2. Какво ви трябва','<ul><li>Двете антени с PoE адаптерите им и екраниран (FTP) кабел за външна употреба.</li><li>Стабилни стойки или мачти'+(P?'. Лъчът е широк около '+bw+'°, но мачта, която се люлее, пак сваля сигнала':'')+'.</li><li>Компас или телефон с компас, инклинометър (приложение), бинокъл, гаечни ключове, водозащитна лента.</li><li>Два компютъра с тази програма (или един, ако имате достъп до двете антени през връзката) и телефони с мобилен интернет (hotspot) за картата и синхронизацията.</li></ul>');
 h+=sec('3. Настройване на антените (у дома, на бюрото)','<p>Най-лесно: раздел <b>„Настройка на антената“</b> - програмата настройва антената вместо вас, стъпка по стъпка, без WinBox и без команди, и после мери скоростта между антените. Отдолу е ръчният начин с WinBox - ако го предпочитате.</p>'+
  '<p>Свържете се с WinBox (бутон Neighbors → по MAC адрес). Потребител <code>admin</code>, паролата е на етикета на антената (Password); ако там няма парола - празно. Обновете RouterOS: System → Packages → Check for updates. По желание започнете на чисто: System → Reset Configuration → No Default Configuration (изтрива всички настройки; после се свързвате само по MAC). После в терминала на WinBox:</p>'+
  cmdBox('Точка за достъп (A) - меню WiFi',linkCmds('ap','wifi',P))+cmdBox('Клиент (Б) - меню WiFi',linkCmds('client','wifi',P))+
  '<p class="note">Ако антената е с другото меню (Wireless):</p>'+cmdBox('Точка за достъп (A) - меню Wireless',linkCmds('ap','wireless',P))+cmdBox('Клиент (Б) - меню Wireless',linkCmds('client','wireless',P))+
  '<ul><li>Паролата на връзката (<code>passphrase</code>) трябва да е <b>еднаква</b> на двете антени. Сменете я и сменете паролата на <code>admin</code>.</li><li>Последните два реда (API и потребител само за четене) не са задължителни, но потребителят само за четене е по-безопасен за програмата.</li><li>IP адресите са пример - сложете различни за двете антени в мрежата си.</li><li>Каналът е 20 MHz, докато насочвате - сигналът е по-стабилен и се отчита по-лесно. Разширявате го накрая (стъпка 9).</li><li>5500 MHz е DFS честота: след включване точката за достъп изчаква 1 минута (на 5600-5650 MHz - 10 минути), преди да излъчва. Това е нормално.</li><li>WiFi е новият пакет (ax модели, интерфейс <code>wifi1</code>), Wireless - старият (<code>wlan1</code>). Програмата чете и двата.</li></ul>'+
  '<p>Проба: включете двете антени на бюрото, обърнати встрани една от друга. В WiFi → Registration на A трябва да се появи Б.</p>');
 h+=sec('4. Планиране (раздел „План и карта“)','<ul><li>Поставете точките A и Б на сателитната карта точно върху сградите или мачтите. Координатите можете да вземете и от Google Maps: десен бутон върху мястото → копирайте числата.</li><li>„Антена над терена“ е височината от земята до антената, включително сградата. Например 4-етажен блок (~12 m) + 3 m мачта = 15 m.</li><li>Височината на терена се взима автоматично от модел на релефа (90 m точност). Ако я знаете по-точно, напишете я.</li><li>Ако има гора или сгради по трасето, попълнете „Дървета/сгради“ (например 15 m). Моделът не вижда отделни дървета.</li><li>Проверете „Видимост“. Трябва поне 60% от зоната на Френел да е свободна'+(P?' (за вас това е '+(P.fMid*0.6).toFixed(1)+' m около линията в средата)':'')+'. Ако не е, вдигнете антените.</li><li>С „Изпрати плана на другия компютър“ имате еднакви данни от двете страни.</li></ul>');
 h+=sec('5. Монтаж','<ul><li>Монтирайте на изчислената височина, на твърда мачта. Двете антени трябва да са завъртени еднакво около оста си (например и двете с кабела надолу). Иначе поляризацията не съвпада и сигналът пада.</li><li>Заземете мачтата. Направете водна примка на кабела (да провисва надолу преди антената), за да не влиза вода в конектора.</li><li>Затегнете стойката само толкова, че антената да не пада, но още да се върти на ръка.</li></ul>');
 h+=sec('6. Грубо насочване','<ul><li><b>Компас:</b> застанете на 2-3 m от металната мачта и от колата. На A насочете към <b>'+az('a')+'</b>, на Б - към <b>'+az('b')+'</b>. Магнитното отклонение вече е отчетено (WMM2025).</li><li><b>Ориентир:</b> на сателитната карта намерете нещо по линията (сграда, кула, дърво) и прицелете антената към него'+(P?'. На '+(P.D/1000).toFixed(2)+' km другата страна обикновено се вижда с бинокъл':'')+'.</li><li><b>Наклон:</b> на A - '+tl('a')+', на Б - '+tl('b')+'. Ползвайте скалата на стойката или приложение за наклон.</li>'+(P?'<li>Грешката трябва да е под половината от ширината на лъча (≈'+Math.round(bw/2)+'°), иначе антените няма да се „видят“.</li>':'')+'</ul>');
 h+=sec('7. Компютрите и програмата','<ul><li>Кабел от порт <b>LAN</b> на PoE адаптера към компютъра. IP адреси не се настройват - програмата сама открива антената (както Neighbors в WinBox). Ако види няколко антени, изберете тази, която е с кабел към този компютър.</li><li>Wi-Fi на компютъра - към hotspot-а на телефона (за картата и за синхронизацията между двата компютъра).</li><li>Пуснете MikrotikAlignFree. Ако Windows попита за защитната стена, натиснете „Разреши“. В „Настройки“ включете синхронизацията, изберете страна (A/Б) и <b>една и съща стая</b> на двата компютъра (бутон „Нов код“, после въведете същия код на другия).</li><li>Ако антената иска парола (от етикета или ваша), програмата я иска веднъж най-отгоре в „Насочване“.</li><li>Ако антената е в друга мрежа и не може да се стигне директно, натиснете „Настрой лаптопа автоматично“ и после „Да“ в прозореца на Windows. Кабелната карта получава временен IP; „Върни на автоматичен IP“ го премахва.</li><li>Горе трябва да светят: <b>Антена ✓</b>, <b>Интернет ✓</b>, <b>Отсреща ✓</b>.</li></ul>');
 h+=sec('8. Фино насочване (раздел „Насочване“)','<ol><li>Насочва само един човек в даден момент. Той натиска <b>„Аз насочвам“</b>, а отсреща излиза надпис да не пипат антената.</li><li>Говорете през програмата (панел „Разговор“): бързи бутони като „Спри - пикът е тук“, текст, а за глас задръжте 🎤 (или клавиша <kbd>Интервал</kbd>), говорете и пуснете. Така телефонът остава само за интернет.</li><li>Започнете от клиента (Б). Точката за достъп стои неподвижно след грубото насочване.</li><li>Включете „Звук“ (клавиш <kbd>M</kbd>): по-висок и по-чест тон означава по-силен сигнал.</li><li><b>Хоризонтално:</b> въртете много бавно, около 1° на стъпка, и изчаквайте 2-3 секунди. Подминете максимума от двете страни и се върнете там, където „dB от пика“ е 0.</li><li><b>Вертикално:</b> същото с наклона.</li><li>Още веднъж хоризонтално и вертикално. След това насочва другата страна (A), а накрая пак малка корекция на Б.</li><li>Цел: около <b>'+(P?f0(P.rxA):'—')+' dBm при A</b> и <b>'+(P?f0(P.rxB):'—')+' dBm при Б</b> (±3-5 dB). Под големите числа програмата показва разликата от очакваното.</li><li><b>Страничен лъч:</b> ако сте с 10-20 dB под очакваното и не можете да го подобрите, вероятно сте хванали страничен лъч. Завъртете по-широко'+(P?' (±'+bw+'°)':'')+' и потърсете по-силен максимум.</li><li>Нулирайте пика („Нулирай пик“, клавиш <kbd>P</kbd>), когато започва другата страна. „Запази точка“ (клавиш <kbd>S</kbd>) записва измерването за протокола.</li><li>Таблица на сигнала: -55 и по-добре - отличен; до -65 - много добър; до -72 - добър; до -78 - приемлив; по-слаб - проверете насочването и препятствията.</li></ol>');
 h+=sec('9. Проверка и разширяване на канала','<ul><li>При меню WiFi RouterOS не дава Ch0/Ch1 и CCQ. Гледайте сигнала и скоростите (tx/rx rate) - трябва да са стабилни и близки до очакваните.</li><li>Тест на скоростта: раздел „Настройка на антената“, стъпка 6 - първо TCP, после UDP (или в WinBox: Tools → Bandwidth Test). <b>Тестът запълва връзката докрай</b>.</li><li>Разширете канала на 40 или 80 MHz (на двете антени: <code>/interface wifi set wifi1 channel.width=20/40mhz</code> или <code>20/40/80mhz</code>) и проверете пак. По-широкият канал дава по-висока скорост, но сигналът пада с около 3 dB за всяко удвояване на ширината.'+(P&&P.spA?' При вашия сигнал се очаква '+('MCS'+P.spA.mcs)+' (≈'+Math.round(P.spA.real)+' Mbps реално при 20 MHz).':'')+'</li></ul>');
 h+=sec('10. Финал','<ul><li>Затягайте болтовете постепенно и следете сигнала. При затягане антената често мърда с 1-2°.</li><li>Защитете конекторите от вода, проверете водната примка и заземяването.</li><li>Запазете точка с крайния сигнал от двете страни и направете протокол („Отчет“). Полезно е да го сравните по-късно, ако връзката се влоши.</li><li>Сменете паролата на <code>admin</code>. Ограничете API услугата само до вашата мрежа: <code>/ip service set api address=192.168.88.0/24</code>, или я изключете, ако не ви трябва.</li></ul>');
 h+=sec('11. Проблеми','<table><tr><th>Проблем</th><th>Какво да проверите</th></tr>'+
  [['Антените не се виждат','Грубото насочване е над половината от лъча; различни SSID, честота или парола; на DFS честота точката за достъп още чака (1-10 мин).'],
   ['„Антена: няма връзка“ / време за изчакване','Грешен IP адрес, компютърът не е в същата мрежа, кабелът е в грешния порт на PoE адаптера (трябва LAN). Опитайте избор на антена от списъка, „Търси по IP адреси“ и „Настрой лаптопа автоматично“.'],
   ['„Отказана връзка“','API услугата е изключена или портът е различен: <code>/ip service print</code>, <code>/ip service enable api</code>.'],
   ['Иска парола','Въведете потребител и парола най-отгоре в „Насочване“. Паролата е на етикета на антената; потребителят трябва да има права <code>read</code> и <code>api</code>.'],
   ['Сигналът е с 10+ dB под очакваното','Страничен лъч, препятствие (дървета), различна поляризация или RouterOS намалява мощността заради country=bulgaria.'],
   ['Сигналът скача','Мачтата се люлее, клони в зоната на Френел или смущения на същия канал - сменете честотата.'],
   ['Добър сигнал, ниска скорост','Пробвайте друг канал (смущения); проверете дали Ethernet портът е на 1 Gbps.'],
   ['„Отсреща: чака...“','Различна стая на двата компютъра, едни и същи страни (A/A) или няма интернет от телефона.'],
   ['Няма звук / микрофон','Браузърът пуска звук и микрофон след разрешение и първо щракване. Проверете силата на звука на компютъра.'],
   ['Картата или релефът не се зареждат','Нужен е интернет (OpenStreetMap, Open-Meteo). Без релеф видимостта не е надеждна.'],
   ['Тестът на скоростта: не прие потребителя','Другата антена не е настроена със същата парола - въведете паролата на admin от етикета ѝ в стъпка 6.']].map(r=>'<tr><td>'+r[0]+'</td><td>'+r[1]+'</td></tr>').join('')+'</table>');
 h+=sec('12. Безопасност','<ul><li>Работата на височина изисква осигуряване и втори човек. Не работете при вятър, дъжд или буря.</li><li>Заземете антената и ползвайте мълниезащита на кабела.</li><li>Не стойте дълго пред излъчваща антена на близко разстояние. Спазвайте местните изисквания за мощност (EIRP) и DFS в 5 GHz.</li><li>Програмата слуша само на този компютър (127.0.0.1). Ползвайте отделен потребител с минимални права на антената.</li></ul>');
 h+=sec('13. Речник','<ul><li><b>dBm</b> - мощност на сигнала; <b>CCQ</b> - качество на връзката в проценти (при пакет wireless); <b>MCS</b> - модулация (по-висок = по-бърз, но по-чувствителен); <b>Ch0/Ch1</b> - сигнал по двете вериги на антената.</li><li><b>Зона на Френел</b> - елипсоид около линията на видимост; препоръчва се поне 60% от нея да е свободна.</li><li><b>Магнитно отклонение</b> - разликата между географския и магнитния север.</li><li><b>AP / station-bridge</b> - точка за достъп / клиент, който прави мост към кабелната мрежа.</li></ul>');
 e.innerHTML=h;$$('#guide-body [data-gc]').forEach(b=>b.onclick=()=>copyText(GC[+b.dataset.gc]))}

/* ---------- старт ---------- */
if(PLAN&&!PLAN.prof)PLAN.prof=[];
syncRole();
window.addEventListener('hashchange',()=>showTab(location.hash.slice(1)));
showTab(location.hash.slice(1)||'aim');loadCfgCache();syncRole();
poll();
</script></body></html>
'''

if __name__ == "__main__":
    main()
