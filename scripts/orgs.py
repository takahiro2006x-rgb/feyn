# -*- coding: utf-8 -*-
"""塾・学校（組織）とコードの発行、運営アカウントの付与をするコマンド（運営だけが使う。管理画面は作らない）。

  py scripts/orgs.py list                          組織の一覧（コード・先生と生徒の人数）
  py scripts/orgs.py create "野村塾"                組織を作り、先生コードと生徒の参加コードを発行する
  py scripts/orgs.py rotate 3 [join|teacher|both]  組織3のコードを作り直す（古いコードは使えなくなる）
  py scripts/orgs.py operators                     運営アカウントの一覧
  py scripts/orgs.py grant-operator you@example.jp  そのアカウントを運営アカウントにする
  py scripts/orgs.py revoke-operator you@example.jp 運営アカウントをやめる

接続先のDB:
  - 環境変数 DATABASE_URL があれば、そのPostgres（本番なら Supabase）
  - 無ければ、手元の feyn.db（SQLite）
  最初に、どちらにつないでいるかを表示する。本番に対して実行するときは、その表示を必ず確かめること。
  （.env に DATABASE_URL を書いていない場合は、PowerShell で $env:DATABASE_URL="..." を設定してから実行する）

初めて実行したときは、アプリの起動時と同じく、足りないテーブル・カラムを追加する（追加だけで、消さない）。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core  # noqa: E402  （読み込むと init_db() が走り、テーブル・カラムがそろう）
import db  # noqa: E402
import schools  # noqa: E402


def describe_target():
    if db.USE_POSTGRES:
        return '接続先: Postgres（環境変数 DATABASE_URL のDB。本番なら Supabase）'
    return f'接続先: 手元のSQLite（{db.DB_PATH}）'


def print_school(school):
    print(f"  ID          : {school['id']}")
    print(f"  名前        : {school['name']}")
    print(f"  先生コード  : {schools.format_code(school['teacher_code'])}   ← 先生だけに渡す")
    print(f"  参加コード  : {schools.format_code(school['join_code'])}   ← 生徒に配る")


def cmd_list(_args):
    rows = schools.list_schools()
    if not rows:
        print('組織はまだありません')
        return 0
    for sc in rows:
        if sc['is_default']:
            print(f"[{sc['id']}] DEFAULT（どこにも入っていない人）  先生 {sc['teachers']}人・生徒 {sc['students']}人"
                  '  ※ここの生徒はどの先生からも見えない')
            continue
        print(f"[{sc['id']}] {sc['name']}  先生 {sc['teachers']}人・生徒 {sc['students']}人")
        print(f"      先生コード {schools.format_code(sc['teacher_code'])} / 参加コード {schools.format_code(sc['join_code'])}")
    return 0


def cmd_create(args):
    school = schools.create_school(args.name)
    print('組織を作りました。')
    print_school(school)
    print()
    print('先生: 新規登録の画面で「先生コード」にこのコードを入れると、この組織の先生になります。')
    print('      すでに先生アカウントがある人は、ダッシュボードの「先生コードを入れる」から入れます。')
    print('生徒: 新規登録の画面の「参加コード」か、登録後にマイページの「塾・学校」から入れます。')
    return 0


def cmd_rotate(args):
    school = schools.rotate_codes(args.school_id, args.which)
    print('コードを作り直しました（古いコードは使えません。すでに入っている人はそのままです）。')
    print_school(school)
    return 0


def cmd_operators(_args):
    rows = schools.list_operators()
    if not rows:
        print('運営アカウントはまだありません')
    for r in rows:
        print(f"[{r['id']}] {r['email']}（{r['name']}・{r['role']}）")
    return 0


def cmd_grant(args):
    user = schools.set_operator(args.email, True)
    print(f"運営アカウントにしました: {user['email']}（{user['name']}）")
    print('ダッシュボード（/dashboard）で、全組織の生徒を見られます。生徒の詳細・会話ログ・CSVを開くと記録が残ります。')
    return 0


def cmd_revoke(args):
    user = schools.set_operator(args.email, False)
    print(f"運営アカウントをやめました: {user['email']}（{user['name']}）")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description='塾・学校（組織）とコード、運営アカウントの管理')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='組織の一覧').set_defaults(func=cmd_list)
    p = sub.add_parser('create', help='組織を作り、コードを発行する')
    p.add_argument('name')
    p.set_defaults(func=cmd_create)
    p = sub.add_parser('rotate', help='コードを作り直す')
    p.add_argument('school_id', type=int)
    p.add_argument('which', nargs='?', default='both', choices=['join', 'teacher', 'both'])
    p.set_defaults(func=cmd_rotate)
    sub.add_parser('operators', help='運営アカウントの一覧').set_defaults(func=cmd_operators)
    p = sub.add_parser('grant-operator', help='運営アカウントにする')
    p.add_argument('email')
    p.set_defaults(func=cmd_grant)
    p = sub.add_parser('revoke-operator', help='運営アカウントをやめる')
    p.add_argument('email')
    p.set_defaults(func=cmd_revoke)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    print(describe_target())
    core.ensure_default_school()
    try:
        return args.func(args)
    except ValueError as e:
        print(f'エラー: {e}')
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
