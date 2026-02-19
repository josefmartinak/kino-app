# Kino App

Připravuje program kina.

## Základní informace

* Verze 0.1
* Aktuálně pouze stažení JSON Kina Frenštát.

## Požadavky na spuštění

* Python

## Instalace a konfigurace

getJSON.py pouštím z cron-job.org takto:

curl -i -X POST "https://api.github.com/repos/josefmartinak/kino-app/dispatches" -H "Accept: application/vnd.github+json" -H "Content-Type: application/json" -H "User-Agent: cronjob-org" -H "Authorization: token CLASSIC_TOKEN" -d '{"event_type":"run_generator"}'

## Autor

* [Josef Martiňák](https://www.wikiskripta.eu/w/User:Josmart)
* MIT License, Copyright (c) 2026 First Faculty of Medicine, Charles University
