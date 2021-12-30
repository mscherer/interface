"""
Gitea-specific utilities
"""
# Bridges software forges to create a distributed software development environment
# Copyright © 2021 Aravinth Manivannan <realaravinth@batsense.net>
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
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from urllib.parse import urlparse

from interface.utils import trim_url
from interface.forges.base import F_D_INVALID_ISSUE_URL


def get_issue_index(issue_url, repo: str) -> int:
    """
    Get isssue index from issue URL
    https://git.batsense.net/{owner}/{repo}/issues/{id} returns {id}
    """
    issue_frag = "issues/"
    if issue_frag not in issue_url:
        raise F_D_INVALID_ISSUE_URL
    parsed = urlparse(trim_url(issue_url))
    path = parsed.path
    fragments = path.split(f"{repo}/{issue_frag}")
    if len(fragments) < 2:
        raise F_D_INVALID_ISSUE_URL

    index = fragments[1]

    if not index.isdigit():
        if "/" in index:
            index = index.split("/")[0]
            if not index.isdigit():
                raise F_D_INVALID_ISSUE_URL
        else:
            raise F_D_INVALID_ISSUE_URL

    return int(index)