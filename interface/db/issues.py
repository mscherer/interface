# Bridges software forges to create a distributed software development environment
# Copyright © 2022 Aravinth Manivannan <realaravinth@batsense.net>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from dataclasses import dataclass
from datetime import datetime
from dateutil.parser import parse as date_parse
from sqlite3 import IntegrityError

from interface.auth import RSAKeyPair
from interface.utils import date_from_string

from .conn import get_db
from .users import DBUser
from .repo import DBRepo
from .interfaces import DBInterfaces
from .webfinger import INTERFACE_BASE_URL, INTERFACE_DOMAIN
from .activity import ActivityType, DBActivity

OPEN = "open"
MERGED = "merged"
CLOSED = "closed"


@dataclass
class DBIssue:
    """Issue information as stored in database"""

    title: str
    description: str
    html_url: str
    created: int
    updated: int
    repo_scope_id: str
    repository: DBRepo
    user: DBUser
    id: int = None
    is_closed: bool = False
    # is_merged only makes sense for PR, therefore it's absence(=None) is
    # used to denote the Issue is an Issue and not a PR
    is_merged: bool = None
    #    -- is_native:
    #        -- false: issue is PR and not merged
    #        -- true: issue is PR and merged
    #        -- false && val(is_closed) == true: issue is PR and is closed
    is_native: bool = True
    private_key: RSAKeyPair = None

    def __set_sqlite_to_bools(self):
        """
        sqlite returns 0 for False and 1 for True and is not automatically typecast
        into bools. This method typecasts all bool values of this class.

        To be invoked by every load_* invocation
        """
        self.is_merged = None if self.is_merged is None else bool(self.is_merged)
        self.is_native = bool(self.is_native)
        self.is_closed = bool(self.is_closed)

    def state(self) -> str:
        """Get state of an issue"""
        # if is_merged is None, then issue is an Issue and not a PR
        # since is_merged doesn't make sense for an aissue
        if self.is_merged is None:
            # Issue can only assume two states: open and close
            # So return state only based on is_closed
            return CLOSED if self.is_closed else OPEN

        # If is_merged is True, then issue is PR and is merged
        if self.is_merged:
            return MERGED

        # if is_merged is False then issue is PR and state needs to be
        # determied based on is_closed
        return CLOSED if self.is_closed else OPEN

    def is_pr(self) -> bool:
        """is Issue a PR"""
        return self.is_merged is not None

    def set_closed(self, updated: str):
        """
        Mark an Issue to be closed.
        """
        self.is_closed = True
        self.updated = updated
        self.__update()

    def set_merged(self, updated: str):
        """
        Set PR as merged
        Since GitHub family of forges close a PR at the same time it's merged,
        the issue will also be marked as closed. However, the ultimiate state of the
        PR is said to be "merged"
        """
        if self.is_pr():
            self.is_merged = True
            self.set_closed(updated)
        #    commented out because self.is_closed() is already setting updated time
        #    and committing changes to database but leaving it here for clarity
        #    self.updated = updated
        #    self.__update()
        else:
            raise TypeError("Issue is not a PR, can't be merged")

    def set_open(self, updated: str):
        """Re-open an issue"""
        # TODO I couldn't find an option to revert a commit on Gitea but GitHub
        # seems to have it. What happens to state in notification if PR is reverted?
        # I'm assuming that the state would be set to not merged
        # and not closed but requires investigation.
        self.is_closed = False
        self.updated = updated
        if self.is_pr():
            self.is_merged = False
        self.__update()

    def __update(self):
        """
        Update changes in database
        Only fields that can be mutated on the forge will be updated in the DB
        """
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE gitea_forge_issues
            SET
                title = ?,
                description = ?,
                updated = ?,
                is_closed = ?,
                is_merged = ?
            WHERE
                id = ?
            """,
            (
                self.title,
                self.description,
                self.updated,
                self.is_closed,
                self.is_merged,
                self.id,
            ),
        )
        conn.commit()

    def save(self):
        """Save Issue to database"""

        issue = self.load(self.repository, self.repo_scope_id)
        if issue is not None:
            self.private_key = issue.private_key
            self.user = issue.user
            self.repository = issue.repository
            self.id = issue.id
            return

        self.user.save()
        self.repository.save()

        conn = get_db()
        cur = conn.cursor()
        count = 0
        while True:
            try:
                self.private_key = RSAKeyPair()

                cur.execute(
                    """
                    INSERT INTO gitea_forge_issues
                        (
                            title, description, html_url, created,
                            updated, is_closed, is_merged, is_native,
                            repo_scope_id, user_id, repository, private_key
                        )
                        VALUES (
                            ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?,
                            (SELECT ID FROM gitea_users WHERE user_id  = ?),
                            ?,
                            ?)
                    """,
                    (
                        self.title,
                        self.description,
                        self.html_url,
                        self.created,
                        self.updated,
                        self.is_closed,
                        self.is_merged,
                        self.is_native,
                        self.repo_scope_id,
                        self.user.user_id,
                        self.repository.id,
                        self.private_key.private_key(),
                    ),
                )
                conn.commit()
                data = cur.execute(
                    """
                    SELECT ID FROM gitea_forge_issues
                    WHERE html_url = ?
                    """,
                    (self.html_url,),
                ).fetchone()
                if data is None:
                    raise ValueError("Issue ID can't be none")
                self.id = data[0]
                DBActivity(
                    user_id=self.user.id,
                    activity=ActivityType.CREATE,
                    created=self.created,
                    issue_id=self.id,
                ).save()

                break
            except IntegrityError as e:
                count += 1
                if count > 5:
                    raise e
                continue
        self.__update()

    @classmethod
    def load(cls, repository: DBRepo, repo_scope_id: str) -> "DBIssue":
        """Load issue from database"""
        conn = get_db()
        cur = conn.cursor()
        data = cur.execute(
            """
         SELECT
             ID,
             title,
             description,
             html_url,
             created,
             updated,
             is_closed,
             is_merged,
             is_native,
             user_id,
             private_key
        FROM
             gitea_forge_issues
        WHERE
            repo_scope_id = ?
        AND
            repository = ?
        """,
            (repo_scope_id, repository.id),
        ).fetchone()
        if data is None:
            return None

        user = DBUser.load_with_db_id(data[9])

        issue = cls(
            id=data[0],
            title=data[1],
            description=data[2],
            html_url=data[3],
            created=data[4],
            updated=data[5],
            is_closed=data[6],
            is_merged=data[7],
            is_native=data[8],
            repo_scope_id=repo_scope_id,
            user=user,
            repository=repository,
            private_key=RSAKeyPair.load_private_from_str(data[10]),
        )

        issue.__set_sqlite_to_bools()
        return issue

    @classmethod
    def load_with_id(cls, db_id: str) -> "DBIssue":
        """
        Load issue from database using database ID
        This ID very different from the one assigned by the forge
        """
        conn = get_db()
        cur = conn.cursor()
        data = cur.execute(
            """
         SELECT
             title,
             description,
             html_url,
             created,
             updated,
             is_closed,
             is_merged,
             is_native,
             repo_scope_id,
             user_id,
             repository,
             private_key
        FROM
             gitea_forge_issues
        WHERE
            ID = ?
        """,
            (db_id,),
        ).fetchone()
        if data is None:
            return None

        user = DBUser.load_with_db_id(data[9])
        repository = DBRepo.load_with_id(data[10])

        issue = cls(
            id=db_id,
            title=data[0],
            description=data[1],
            html_url=data[2],
            created=data[3],
            updated=data[4],
            is_closed=data[5],
            is_merged=data[6],
            is_native=data[7],
            repo_scope_id=data[8],
            user=user,
            repository=repository,
            private_key=RSAKeyPair.load_private_from_str(data[11]),
        )
        issue.__set_sqlite_to_bools()
        return issue

    @classmethod
    def load_with_html_url(cls, html_url: str) -> "DBIssue":
        """
        Load issue from database using HTML URL
        """
        conn = get_db()
        cur = conn.cursor()
        data = cur.execute(
            """
         SELECT
             title,
             description,
             ID,
             created,
             updated,
             is_closed,
             is_merged,
             is_native,
             repo_scope_id,
             user_id,
             repository,
             private_key
        FROM
             gitea_forge_issues
        WHERE
            html_url = ?
        """,
            (html_url,),
        ).fetchone()
        if data is None:
            return None

        user = DBUser.load_with_db_id(data[9])
        repository = DBRepo.load_with_id(data[10])

        issue = cls(
            html_url=html_url,
            title=data[0],
            description=data[1],
            id=data[2],
            created=data[3],
            updated=data[4],
            is_closed=data[5],
            is_merged=data[6],
            is_native=data[7],
            repo_scope_id=data[8],
            user=user,
            repository=repository,
            private_key=RSAKeyPair.load_private_from_str(data[11]),
        )
        issue.__set_sqlite_to_bools()
        return issue

    def actor_name(self) -> str:
        name = f"{self.repository.actor_name()}!issue!{self.repo_scope_id}"
        return name

    def actor_url(self) -> str:
        act_url = f"{INTERFACE_BASE_URL}/i/{self.actor_name()}"
        return act_url

    def to_actor(self):
        act_url = self.actor_url()
        act_name = self.actor_name()

        actor = {
            "@context": [
                "https://www.w3.org/ns/activitystreams",
                "https://w3id.org/security/v1",
            ],
            "id": act_url,
            "type": "Group",
            "preferredUsername": act_name,
            "name": act_name,
            "summary": f"<p>{self.description}</p>",
            "url": act_url,
            "inbox": f"{act_url}/inbox",
            "outbox": f"{act_url}/outbox",
            "followers": f"{act_url}/followers",
            "following": f"{act_url}/following",
            "publicKey": {
                "id": f"{act_url}#main-key",
                "owner": act_url,
                "publicKeyPem": self.private_key.to_json_key(),
            },
            "manuallyApprovesFollowers": False,
            "discoverable": True,
            "published": "2016-03-16T00:00:00Z",
            "alsoKnownAs": [act_url],
            "tag": [],
            "endpoints": {"sharedInbox": f"{act_url}/inbox"},
            "icon": {
                "type": "Image",
                "mediaType": "image/png",
                "url": self.repository.owner.avatar_url,
            },
            "image": {
                "type": "Image",
                "mediaType": "image/png",
                "url": self.repository.owner.avatar_url,
            },
        }

        return actor

    def webfinger_subject(self) -> str:
        subject = f"acct:{self.actor_name()}@{INTERFACE_DOMAIN}"
        return subject

    def webfinger(self):
        act_url = self.actor_url()
        resp = {
            "subject": self.webfinger_subject(),
            "links": [
                {
                    "rel": "self",
                    "type": "application/activity+json",
                    "href": act_url,
                },
                {
                    "rel": "http://webfinger.net/rel/profile-page",
                    "type": "text/html",
                    "href": act_url,
                },
            ],
        }
        return resp

    @staticmethod
    def split_actor_name(name) -> (str, str):
        """return format: (owner, repository_name, issue_id)"""
        if "!" not in name:
            raise ValueError("Invalid actor name")

        name_parts = name.split("!")
        owner = name_parts[1]
        name = name_parts[2]
        issue_id = name_parts[4]
        return (owner, name, issue_id)

    @classmethod
    def from_actor_name(cls, name) -> "DBIssue":
        (owner, name, repo_scope_id) = cls.split_actor_name(name)
        repo = DBRepo.load(name=name, owner=owner)
        issue = cls.load(repo, repo_scope_id=repo_scope_id)
        return issue
