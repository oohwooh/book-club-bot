from os import getenv

from sqlalchemy import *
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from datetime import datetime
from typing import Optional
import random
from olclient.openlibrary import OpenLibrary
openlibrary = OpenLibrary()
from telegram import Update
from telegram.utils.helpers import escape_markdown
from utils import format_date

postgres_db = {
    "drivername": "postgresql",
    "username": getenv("DB_USERNAME"),
    "password": getenv("DB_PASSWORD"),
    "database": getenv("DB_DB"),
    "host": getenv("DB_HOST"),
    "port": 5432
}
postgres_url = URL(**postgres_db)
engine = create_engine(postgres_url)
metadata = MetaData()

Base = declarative_base(bind=engine, metadata=metadata)


task_to_meeting_table = Table('association', Base.metadata,
                              Column('scheduled_offset_task_id', ForeignKey('scheduled_offset_task.id')),
                              Column('meeting_id', ForeignKey('meeting.id'))
                              )
class ScheduledOffsetTask(Base):
    __tablename__ = "scheduled_offset_task"

    id = Column(Integer, primary_key=True)
    club_id = Column(Integer, ForeignKey("club.id"))
    club = relationship("Club", back_populates="scheduled_offset_tasks")
    action = Column(String)
    when = Column(String)
    run_on_meetings = relationship("Meeting", secondary=task_to_meeting_table, back_populates="complete_offset_tasks")


class ScheduledRepeatingTask(Base):
    __tablename__ = "scheduled_repeating_task"

    id = Column(Integer, primary_key=True)
    club_id = Column(Integer, ForeignKey("club.id"))
    club = relationship("Club", back_populates="scheduled_repeating_tasks")
    action = Column(String)
    when = Column(String)


class Meeting(Base):
    __tablename__ = "meeting"

    id = Column(Integer, primary_key=True)
    club_id = Column(Integer, ForeignKey("club.id"))
    club = relationship("Club", back_populates="meetings")
    date_time = Column(DateTime)
    book_olid = Column(String)
    book_pages = Column(String)
    complete_offset_tasks = relationship("ScheduledOffsetTask", secondary=task_to_meeting_table, back_populates="run_on_meetings")

    def __str__(self):
        book = openlibrary.get(self.book_olid) if self.book_olid else None
        return f'''
{format_date(self.date_time) if self.date_time else 'Date TBA'}
Book: {f"[{escape_markdown(book.title)}](https://openlibrary.org/books/{book.olid}/)" if book else 'TBA'}
Pages: {self.book_pages if self.book_pages else 'TBA'}

To delete this meeting: `/delete_meeting {self.id}`
To update this meetings book: `/set_meeting_book {self.id} [OLID]`
To update this meetings pages: `/set_meeting_pages {self.id} [pages]`
'''

class Suggestion(Base):
    __tablename__ = "suggestion"

    id = Column(Integer, primary_key=True)
    club_id = Column(Integer, ForeignKey("club.id"))
    club = relationship("Club", back_populates="suggestions")
    last_voted_on = Column(DateTime)
    book_olid = Column(String)
    suggested_by = Column(String)


class Club(Base):
    __tablename__ = "club"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    chat_id = Column(String, unique=True)
    meetings = relationship("Meeting")
    suggestions = relationship("Suggestion")
    admins = relationship("Admin")
    poll_msg_id = Column(String)
    scheduled_offset_tasks = relationship("ScheduledOffsetTask")
    scheduled_repeating_tasks = relationship("ScheduledRepeatingTask")

    def get_next_meeting(self) -> Optional[Meeting]:
        # This is not very effecient but it shouldn't be a problem
        for meeting in sorted(self.meetings, key=lambda m: m.date_time):
            if meeting.date_time and meeting.date_time > datetime.now():
                return meeting
        return None

    def pick_n_suggestions(self, n) -> [Suggestion]:
        """Randomly picks up to n Suggestions"""
        if len(self.suggestions) <= n:
            return self.suggestions
        return random.sample(self.suggestions, n)

    def get_chunked_suggestion_strs(self, update: Update, page: int, n:int=4) -> [list]:
        suggestions_strs = []
        next_meeting = self.get_next_meeting()
        for s in self.suggestions[page*n:(page+1)*n]:
            b = openlibrary.get(s.book_olid)
            suggestions_strs.append(f'''
- [{escape_markdown(b.title)}](https://openlibrary.org/books/{b.olid}) by {", ".join([a.name for a in b.authors])}
    Suggested by: {escape_markdown(update.effective_chat.get_member(s.suggested_by).user.first_name)}
    {f"Manually select for next meeting: `/smb {next_meeting.id} {s.id}`" if next_meeting else ""}
    Remove this suggestion: `/ds {s.id}`''')
        return ''.join(suggestions_strs)

class Admin(Base):
    __tablename__ = "admin"

    id = Column(Integer, primary_key=True)
    club_id = Column(Integer, ForeignKey("club.id"))
    club = relationship("Club", back_populates="admins")
    user_id = Column(String)


def session_creator() -> Session:
    session = sessionmaker(bind=engine)
    return session()


global_session: Session = session_creator()