import os
from flask import Flask, request, abort, jsonify, current_app
from bot import db
from bot import utils
from bot.directive import Directive
from bot.context import Context, PrivateContext, GroupContext
from bot.scheduled_tasks import init_background_tasks
from datetime import date


def create_app(config=None):
    app = Flask(__name__)

    if config:
        # load the config if passed in
        app.config.from_mapping(config)
    else:
        app.config.from_mapping(
            DATABASE=os.path.join(app.instance_path, 'database.db')
        )
        if not os.path.exists(app.instance_path):
            os.mkdir(app.instance_path)
        app.config.from_pyfile(os.path.join(os.path.dirname(app.root_path), 'settings.cfg'))

    # print(os.getcwd())
    db.init_database(app)

    app.route('/', methods=['POST'])(handler)
    app.route('/webhook', methods=['POST'])(webhook_handler)

    app.teardown_appcontext(db.close_db)

    init_background_tasks(app)

    return app


def handler():
    payload = request.json
    post_type = payload.get("post_type")

    if post_type != "message":
        abort(400)

    if payload['message_type'] != 'group' and payload['message_type'] != 'private':
        abort(400)

    if payload['message_type'] == 'group' and \
            payload['group_id'] not in current_app.config['ALLOWED_GROUP']:
        abort(400)

    if payload['message_type'] == 'group':
        context = GroupContext(payload)
    elif payload['message_type'] == 'private':
        context = PrivateContext(payload)

    pre_process(context)

    # map command to Directive class and execute it.
    try:
        if hasattr(Directive, context.directive):
            obj = Directive(context)
            response = getattr(obj, context.directive)()
        else:
            response = ''
    except AttributeError:  # if a message is not a directive
        response = ''

    return jsonify(response) if isinstance(response, dict) else ''


# TODO 异步执行
def pre_process(context: Context):
    utils.log(context)
    utils.accumulate_exp(context)
    utils.randomly_save_message_to_treehole(context)

    if context.message_type == 'group':
        utils.find_cai(context)

        if context.group_id == current_app.config['FORWARDED_QQ_GROUP_ID']:
            utils.send_to_tg(context)

        if context.group_id == current_app.config['GHS_NOTIFY_GROUP']:
            if utils.detect_blue(context):
                utils.send(context, "gkd gkd ~")
                c = utils.get_db()
                data = c.execute("select * from misc where key = 'last_ghs_date'").fetchone()
                today = date.fromtimestamp(context.time).isoformat()
                if data is None:
                    c.execute("insert into misc values ('last_ghs_date', ?)", (today,))
                elif data['value'] == date.today().isoformat():
                    return
                else:
                    c.execute("update misc set value = ? where key = 'last_ghs_date'", (today,))
                c.commit()


def webhook_handler():
    context = GroupContext.build('', group_id=current_app.config['WEBHOOK_NOTIFICATION_GROUP'])

    # DOC: https://developer.github.com/webhooks/event-payloads/
    payload = request.json
    if request.headers.get("X-GitHub-Event") == 'push':
        commits = payload['commits']
        message = f"{payload['sender']['login']} has pushed {len(commits)} commit(s)" \
                  f" to my repository:"
        for commit in commits:
            message += f"\n{commit['id'][:6]} {commit['message']}"
        utils.send(context, message)
    elif request.headers.get("X-GitHub-Event") == 'pull_request':
        message = f"{payload['sender']['login']} has {payload['action']} a pull request: " \
                  f"{payload['pull_request']['title']}.\n" \
                  f"For details see: {payload['pull_request']['html_url']}"
        utils.send(context, message)
    return ""
