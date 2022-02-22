from dotenv import load_dotenv
load_dotenv()
from os import getenv
from db.models import session_creator, Session, Club, Admin, Suggestion, Meeting, ScheduledOffsetTask, ScheduledRepeatingTask
from functools import wraps
import logging
from math import ceil
from utils import format_date
from dateutil.parser import parse, ParserError
from dateutil.tz import gettz
from telegram import Update, ForceReply, ParseMode, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardButton, InlineKeyboardMarkup, error
from telegram.ext import Updater, CommandHandler, InlineQueryHandler, Filters, CallbackContext, CallbackQueryHandler, MessageHandler
from telegram.utils.helpers import escape_markdown
from datetime import datetime, timedelta
from durations import Duration
import openlibrary as ol
bookSearch = ol.BookSearch()
from olclient.openlibrary import OpenLibrary
openlibrary = OpenLibrary()
from telegram_bot_pagination import InlineKeyboardPaginator


tzinfos = {
    "EST": gettz("America/New_York"),
    "EDT": gettz("America/New_York"),
    "CST": gettz("America/Chicago"),
    "CDT": gettz("America/Chicago"),
    "MST": gettz("America/Denver"),
    "MDT": gettz("America/Denver"),
    "PST": gettz("America/Los_Angeles"),
    "PDT": gettz("America/Los_Angeles"),
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)


def only_in_group_with_club(func):
    @wraps(func)
    def wrapped(update, ctx, *args, **kwargs):
        session = session_creator()
        club = session.query(Club).filter_by(chat_id=str(update.effective_chat.id)).first()
        if not club:
            session.close()
            return
        out = func(update, ctx, session, club, *args, **kwargs)
        session.close()
        return out
    return wrapped


def only_admin(func):
    @wraps(func)
    def wrapped(update: Update, ctx: CallbackContext, session: Session, club: Club, *args, **kwargs):
        admins = [a.user_id for a in club.admins]
        if str(update.effective_user.id) not in admins:
            update.effective_chat.send_message('This command is for admins only!')
            return
        return func(update, ctx, session, club, *args, **kwargs)
    return wrapped


def create_club(update: Update, ctx: CallbackContext) -> None:
    session = session_creator()
    user = update.effective_user
    club = Club(name=' '.join(ctx.args), chat_id=update.effective_chat.id)
    session.add(club)
    club.admins.append(Admin(user_id=user.id))
    session.commit()
    session.close()
    update.effective_chat.send_message("Book club created!")


@only_in_group_with_club
@only_admin
def delete_club(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    keyboard = [[
        InlineKeyboardButton("Yes", callback_data='dy'),
        InlineKeyboardButton("No", callback_data='dn')
    ]]
    update.effective_chat.send_message(f'Are you sure you want to delete {club.name}?', reply_markup=InlineKeyboardMarkup(keyboard))


@only_in_group_with_club
@only_admin
def delete_confirm(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    query = update.callback_query
    if query.data == 'dn':
        update.effective_chat.send_message('Action cancelled!')
        query.message.delete()
        query.answer()
    elif query.data == 'dy':
        session.delete(club)
        session.commit()
        update.effective_chat.send_message('Book club deleted!')
        query.message.delete()
        query.answer()


@only_in_group_with_club
@only_admin
def schedule_meeting(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    date = None
    try:
        date = parse(" ".join(ctx.args).upper(), tzinfos=tzinfos)
    except ParserError:
        update.effective_chat.send_message(
            'I was unable to parse that date! Suggested format: `/schedule_meeting February 20th 6:30 pm CST`',
            parse_mode=ParseMode.MARKDOWN)
        return
    keyboard = [
        [
            InlineKeyboardButton("Yes", callback_data=f'sy{date.isoformat()}'),
            InlineKeyboardButton("No", callback_data=f'sn')
        ]
    ]
    update.effective_chat.send_message(f'Are you sure you want to schedule a meeting for {format_date(date)}?', reply_markup=InlineKeyboardMarkup(keyboard))


@only_in_group_with_club
@only_admin
def schedule_confirm(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    query = update.callback_query
    if query.data == 'sn':
        update.effective_chat.send_message('Action cancelled!')
        query.message.delete()
        query.answer()
    elif query.data.startswith('sy'):
        date = datetime.fromisoformat(query.data.replace('sy', '', 1))
        update.effective_chat.send_message(f'Meeting scheduled for {format_date(date)}!')
        club.meetings.append(Meeting(date_time=date))
        session.commit()
        query.message.delete()
        query.answer()


@only_in_group_with_club
@only_admin
def set_meeting_book(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    meeting_id = int(ctx.args[0])
    book_olid = ctx.args[1]
    if not book_olid.startswith('OL'):
        suggestion = session.query(Suggestion).get(book_olid)
        if suggestion:
            book_olid = suggestion.book_olid
        else:
            update.effective_chat.send_message(f"Suggestion {suggestion.id} not found")
            return
    meeting = session.query(Meeting).get(meeting_id)
    if not meeting or meeting.club_id != club.id:
        update.effective_chat.send_message('That meeting does not belong to this book club!')
        return
    book = openlibrary.get(book_olid)
    if not book:
        update.effective_chat.send_message(f'Book with OLID {book_olid} not found on OpenLibrary!')
        return
    meeting.book_olid = book_olid
    suggestions_of_book = session.query(Suggestion).filter_by(book_olid=book_olid)
    for s in suggestions_of_book:
        session.delete(s)
    session.commit()
    update.effective_chat.send_message(f'''
Book for meeting (id no. {meeting.id}) set to {book.title}!
Don't forget to set the pages for this meeting with `/set_meeting_pages {meeting_id} [pages]`''',
                                       parse_mode=ParseMode.MARKDOWN)


@only_in_group_with_club
@only_admin
def set_meeting_pages(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    meeting_id = ctx.args[0]
    pages = ' '.join(ctx.args[1:])
    meeting = session.query(Meeting).get(meeting_id)
    if not meeting or meeting.club_id != club.id:
        update.effective_chat.send_message('That meeting does not belong to this book club!')
        return
    meeting.book_pages = pages
    session.commit()
    update.effective_chat.send_message(f'Pages for meeting (id no. {meeting.id}) set to {pages}!')


@only_in_group_with_club
@only_admin
def delete_meeting(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    meeting_id = ctx.args[0]
    meeting = session.query(Meeting).get(meeting_id)
    if not meeting or meeting.club_id != club.id:
        update.effective_chat.send_message('That meeting does not belong to this book club!')
        return
    session.delete(meeting)
    session.commit()
    update.effective_chat.send_message('Meeting deleted!')


@only_in_group_with_club
def next_meeting(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    meeting = club.get_next_meeting()
    keyboard = [
        [
            InlineKeyboardButton("Suggest a book", switch_inline_query_current_chat='')
        ]
    ]
    if not meeting:
        update.effective_chat.send_message('''
No upcoming meetings are scheduled!
To schedule a meeting use: `/schedule_meeting [date]`''', parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    update.effective_chat.send_message(f'''
Next meeting for {club.name}:
{str(meeting)}''', parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


@only_in_group_with_club
def suggest(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    keyboard = [
        [
            InlineKeyboardButton("Suggest a book", switch_inline_query_current_chat='')
        ]
    ]
    if len(ctx.args) == 0:
        update.effective_chat.send_message('Click the button below to search for a book!', reply_markup=InlineKeyboardMarkup(keyboard))
    suggestion = openlibrary.get(ctx.args[0])
    if not suggestion:
        update.effective_chat.send_message("No book found with that ID - click the button below to search!", reply_markup=InlineKeyboardMarkup(keyboard))
    club.suggestions.append(Suggestion(book_olid=str(suggestion.olid), suggested_by=str(update.effective_user.id)))
    session.commit()
    update.effective_chat.send_photo(
        photo=f'https://covers.openlibrary.org/b/olid/{suggestion.olid}-L.jpg',
        caption=f'''
{update.effective_user.first_name} suggested:

[{escape_markdown(suggestion.title)}](https://openlibrary.org/books/{suggestion.olid}) by {', '.join([a.name for a in suggestion.authors])}

{suggestion.description or ''}
''', parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


@only_in_group_with_club
@only_admin
def delete_suggestion(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    suggestion = session.query(Suggestion).get(ctx.args[0])
    session.delete(suggestion)
    session.commit()
    update.effective_chat.send_message("Suggestion deleted!")


@only_in_group_with_club
def suggestions(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    if len(club.suggestions) == 0:
        update.effective_chat.send_message('There are no book suggestions!')
        return
    paginator = InlineKeyboardPaginator(
        ceil(len(club.suggestions) / 4),
        data_pattern='psug#{page}'
    )
    update.effective_chat.send_message(
        club.get_chunked_suggestion_strs(update, 0),
        reply_markup=paginator.markup,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True)


@only_in_group_with_club
def suggestions_page_callback(update: Update, ctx: CallbackContext, session: Session, club: Club) -> None:
    query = update.callback_query
    page = int(query.data.split('#')[1])
    paginator = InlineKeyboardPaginator(
        ceil(len(club.suggestions) / 4),
        current_page=page,
        data_pattern='psug#{page}'
    )

    query.edit_message_text(
        text=club.get_chunked_suggestion_strs(update, page-1),
        reply_markup=paginator.markup,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )
    query.answer()


@only_in_group_with_club
@only_admin
def open_poll(update: Update, ctx: CallbackContext, session: Session, club: Club):
    if club.poll_msg_id:
        update.effective_chat.send_message(
            'There can only be one active suggestion poll at a time. To close the current poll, use /close_poll')
    candidates = club.pick_n_suggestions(10)
    if len(candidates) < 2:
        update.effective_chat.send_message('Too few suggestions to run a poll!')
        return
    options = set()
    for candidate in candidates:
        session.query(Suggestion).get(candidate.id).last_voted_on = datetime.now()
        book = openlibrary.get(candidate.book_olid)
        if book:
            option = f'{book.title} ({book.olid})'
            if len(option) > 100:
                # 6 is length of space, parenthesis, and elipsis
                option = f'{book.title[:-(100 - len(option) - len(book.olid) - 6)]}... ({book.olid})'
            options.add(option)
    poll = update.effective_chat.send_poll(
        question=f'Vote for our next book!',
        options=list(options),
        allows_multiple_answers=True
    )
    club.poll_msg_id = poll.message_id
    session.commit()
    poll.pin()


@only_in_group_with_club
@only_admin
def close_poll(update: Update, ctx: CallbackContext, session: Session, club: Club):
    if not club.poll_msg_id:
        update.effective_chat.send_message(
            'There is no poll currently active!'
        )
        return
    poll = ctx.bot.stop_poll(chat_id=update.effective_chat.id, message_id=int(club.poll_msg_id))
    winner = poll.options[0]
    for option in poll.options:
        if option.voter_count > winner.voter_count:
            winner = option
    olid = winner.text.split(' ')[-1].replace('(', '').replace(')', '')
    book = openlibrary.get(olid)
    update.effective_chat.send_message(f'''
Book selected: [{escape_markdown(book.title)}](https://openlibrary.org/books/{book.olid}) by {', '.join([a.name for a in book.authors])}
{f"Set this as the book for the next meeting: `/smb {club.get_next_meeting().id} {book.olid}`" if club.get_next_meeting() else ""}
''', reply_to_message_id=int(club.poll_msg_id), parse_mode=ParseMode.MARKDOWN)
    club.poll_msg_id = None
    session.commit()


def inlinequery(update: Update, ctx: CallbackContext) -> None:
    query = update.inline_query.query
    if query == "":
        update.inline_query.answer(results=[])
        return
    results = [
        InlineQueryResultArticle(
            id=book.key,
            title=book.title,
            description=f'By {book.author if type(book.author) is not list else ", ".join(book.author)}',
            thumb_url=f'https://covers.openlibrary.org/b/olid/{book.cover_edition_key}-M.jpg',
            input_message_content=InputTextMessageContent(message_text=f'/suggest {book.cover_edition_key}')
        ) for book in bookSearch.get_by_title(query)]
    update.inline_query.answer(results[:20])


def check_offset_tasks(ctx: CallbackContext) -> None:
    session = session_creator()
    clubs = session.query(Club).all()
    for club in clubs:
        next_meeting = club.get_next_meeting()
        if next_meeting and next_meeting.date_time:
            for task in club.scheduled_offset_tasks:
                if next_meeting not in task.run_on_meetings:
                    earliest_proc = next_meeting.date_time.timestamp() - Duration(task.when).to_seconds()
                    if earliest_proc <= datetime.now().timestamp() and datetime.now().timestamp() - earliest_proc < 60 * 60:
                        if task.action == 'nag':
                            parsed_durations = Duration(task.when).parsed_durations
                            time_until = ', '.join([f'{d.value:g} {d.scale.representation.long_plural if d.value > 1 else d.scale.representation.long_singular}' for d in parsed_durations])
                            keyboard = [
                                [
                                    InlineKeyboardButton("Suggest a book", switch_inline_query_current_chat='')
                                ]
                            ]
                            msg = ctx.bot.send_message(chat_id=club.chat_id, text=f'''
Reminder: {club.name} is meeting {f"in {time_until}" if earliest_proc != next_meeting.date_time.timestamp() else "now"}!
{str(next_meeting)}''', parse_mode=ParseMode.MARKDOWN, keyboard=InlineKeyboardMarkup(keyboard))
                            try:
                                msg.pin()
                            except:
                                pass
                        task.run_on_meetings.append(next_meeting)
                        session.commit()


@only_in_group_with_club
@only_admin
def schedule_offset_task(update: Update, ctx: CallbackContext, session: Session, club: Club):
    action = ctx.args[0]
    when = ctx.args[1]
    club.scheduled_offset_tasks.append(ScheduledOffsetTask(action=action, when=when))
    session.commit()
    update.effective_chat.send_message('Scheduled!')


@only_in_group_with_club
@only_admin
def scheduled_tasks(update: Update, ctx: CallbackContext, session: Session, club: Club):
    task_strs = []
    for task in club.scheduled_offset_tasks:
        task_strs.append(f'''
{task.action} {task.when} before meeting
    Delete this task: `/delete_offset_task {task.id}`''')
    update.effective_chat.send_message(f'''
Scheduled tasks for {club.name}:
{''.join(task_strs)}''', parse_mode=ParseMode.MARKDOWN)


@only_in_group_with_club
@only_admin
def delete_offset_task(update: Update, ctx: CallbackContext, session: Session, club: Club):
    task = session.query(ScheduledOffsetTask).get(ctx.args[0])
    if not task or task not in club.scheduled_offset_tasks:
        update.effective_chat.send_message('That task does not belong to this book club!')
        return
    session.delete(task)
    session.commit()
    update.effective_chat.send_message('Task deleted!')


@only_in_group_with_club
@only_admin
def add_admin(update: Update, ctx: CallbackContext, session: Session, club: Club):
    user = None
    try:
        user = update.effective_chat.get_member(ctx.args[0])
    except error.BadRequest:
        update.effective_chat.send_message(f'User with telegram ID {ctx.args[0]} not found!')
        return
    admins = [a.user_id for a in club.admins]
    if str(user.user.id) in admins:
        update.effective_chat.send_message(f'That person is already an admin!')
    club.admins.append(Admin(user_id=user.user.id))
    session.commit()
    update.effective_chat.send_message(f'Added {user.user.first_name} as an admin!')


def get_id(update: Update, ctx: CallbackContext):
    if not update.effective_message.reply_to_message:
        update.effective_chat.send_message('Must be sent as a reply to a messsage!')
        return
    user = update.effective_message.reply_to_message.from_user
    update.effective_chat.send_message(f'Telegram user ID of {user.first_name} is {user.id}')


filters = Filters.chat_type.group | Filters.chat_type.supergroup


def main() -> None:
    updater = Updater(getenv("BOT_TOKEN"))
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler(["create", "create_club"], create_club, filters=filters))
    dispatcher.add_handler(CommandHandler(["delete", "delete_club"], delete_club, filters=filters))
    dispatcher.add_handler(CommandHandler("suggest", suggest, filters=filters))
    dispatcher.add_handler(CommandHandler("suggestions", suggestions, filters=filters))
    dispatcher.add_handler(CommandHandler("schedule_meeting", schedule_meeting, filters=filters))
    dispatcher.add_handler(CommandHandler(["meeting", "next_meeting"], next_meeting, filters=filters))
    dispatcher.add_handler(CommandHandler(["set_meeting_book", "smb"], set_meeting_book, filters=filters))
    dispatcher.add_handler(CommandHandler(["set_meeting_pages", "smp"], set_meeting_pages, filters=filters))
    dispatcher.add_handler(CommandHandler("delete_meeting", delete_meeting, filters=filters))
    dispatcher.add_handler(CommandHandler(["delete_suggestion", "ds"], delete_suggestion, filters=filters))
    dispatcher.add_handler(CommandHandler("open_poll", open_poll, filters=filters))
    dispatcher.add_handler(CommandHandler("close_poll", close_poll, filters=filters))
    dispatcher.add_handler(CommandHandler("schedule_offset_task", schedule_offset_task, filters=filters))
    dispatcher.add_handler(CommandHandler("scheduled_tasks", scheduled_tasks, filters=filters))
    dispatcher.add_handler(CommandHandler("delete_offset_task", delete_offset_task, filters=filters))
    dispatcher.add_handler(CommandHandler("add_admin", add_admin, filters=filters))
    dispatcher.add_handler(CommandHandler("get_id", get_id, filters=filters))
    dispatcher.add_handler(CallbackQueryHandler(schedule_confirm, pattern=r's.*'))
    dispatcher.add_handler(CallbackQueryHandler(delete_confirm, pattern=r'd.*'))
    dispatcher.add_handler(CallbackQueryHandler(suggestions_page_callback, pattern=r'^psug#'))
    dispatcher.add_handler(InlineQueryHandler(inlinequery))
    updater.job_queue.run_repeating(
        callback=check_offset_tasks, interval=60
    )
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()