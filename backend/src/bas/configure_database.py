"""Local administration/recovery: python -m bas.configure_database token|prepare|local."""
import argparse
import os
from bas.infrastructure.db_settings import DatabaseSettings, schema_report
from bas.infrastructure.database import make_engine, metadata
from sqlalchemy import text


def main():
    parser=argparse.ArgumentParser(description='KUMOsystems: локальное управление подключением к БД')
    parser.add_argument('action',choices=['token','prepare','local'])
    action=parser.parse_args().action
    manager=DatabaseSettings()
    if action=='token':
        manager.initialize_admin()
        print(os.getenv('KUMO_ADMIN_TOKEN') or (manager.directory/'admin-token').read_text(encoding='utf-8'))
        return
    if action=='local':
        print(manager.reset()['message'])
        return
    engine=make_engine()
    try:
        if engine.dialect.name!='postgresql':
            raise ValueError('Для подготовки нужна настройка PostgreSQL через BAS_DATABASE_URL или database.json.')
        with engine.begin() as connection:
            report=schema_report(connection,metadata)
            if report['incompatible_tables']:
                raise ValueError('В схеме есть несовместимые таблицы; нужна миграция администратора.')
            connection.execute(text('CREATE SCHEMA IF NOT EXISTS kumosystems'))
            metadata.create_all(connection)
        print('Схема kumosystems подготовлена. Существующие записи не изменены.')
    finally:
        engine.dispose()


if __name__=='__main__':
    try: main()
    except Exception:
        raise SystemExit('Не удалось выполнить действие. Проверьте конфигурацию, сеть, TLS и права БД. Секреты и ошибки драйвера не выводятся.') from None
