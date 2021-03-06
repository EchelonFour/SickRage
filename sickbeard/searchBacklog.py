# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of Sick Beard.
#
# Sick Beard is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Sick Beard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Sick Beard.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import with_statement

import datetime
import threading

import sickbeard

from sickbeard import db, scheduler
from sickbeard import search_queue
from sickbeard import logger
from sickbeard import ui
from sickbeard import common

class BacklogSearchScheduler(scheduler.Scheduler):
    def forceSearch(self):
        self.action._set_lastBacklog(1)
        self.lastRun = datetime.datetime.fromordinal(1)

    def nextRun(self):
        if self.action._lastBacklog <= 1:
            return datetime.date.today()
        else:
            return datetime.date.fromordinal(self.action._lastBacklog + self.action.cycleTime)


class BacklogSearcher:
    def __init__(self):

        self._lastBacklog = self._get_lastBacklog()
        self.cycleTime = 7
        self.lock = threading.Lock()
        self.amActive = False
        self.amPaused = False
        self.amWaiting = False

        self._resetPI()

    def _resetPI(self):
        self.percentDone = 0
        self.currentSearchInfo = {'title': 'Initializing'}

    def getProgressIndicator(self):
        if self.amActive:
            return ui.ProgressIndicator(self.percentDone, self.currentSearchInfo)
        else:
            return None

    def am_running(self):
        logger.log(u"amWaiting: " + str(self.amWaiting) + ", amActive: " + str(self.amActive), logger.DEBUG)
        return (not self.amWaiting) and self.amActive

    def searchBacklog(self, which_shows=None):

        if which_shows:
            show_list = which_shows
        else:
            show_list = sickbeard.showList

        if self.amActive:
            logger.log(u"Backlog is still running, not starting it again", logger.DEBUG)
            return

        self._get_lastBacklog()

        curDate = datetime.date.today().toordinal()
        fromDate = datetime.date.fromordinal(1)

        if not which_shows and not curDate - self._lastBacklog >= self.cycleTime:
            logger.log(u"Running limited backlog on recently missed episodes only")
            fromDate = datetime.date.today() - datetime.timedelta(days=7)

        self.amActive = True
        self.amPaused = False

        # go through non air-by-date shows and see if they need any episodes
        for curShow in show_list:

            if curShow.paused:
                continue

            segments = self._get_segments(curShow, fromDate)

            if len(segments):
                backlog_queue_item = search_queue.BacklogQueueItem(curShow, segments)
                sickbeard.searchQueueScheduler.action.add_item(backlog_queue_item)  #@UndefinedVariable
            else:
                logger.log(u"Nothing needs to be downloaded for " + str(curShow.name) + ", skipping this season",
                           logger.DEBUG)

        # don't consider this an actual backlog search if we only did recent eps
        # or if we only did certain shows
        if fromDate == datetime.date.fromordinal(1) and not which_shows:
            self._set_lastBacklog(curDate)

        self.amActive = False
        self._resetPI()

    def _get_lastBacklog(self):

        logger.log(u"Retrieving the last check time from the DB", logger.DEBUG)

        myDB = db.DBConnection()
        sqlResults = myDB.select("SELECT * FROM info")

        if len(sqlResults) == 0:
            lastBacklog = 1
        elif sqlResults[0]["last_backlog"] == None or sqlResults[0]["last_backlog"] == "":
            lastBacklog = 1
        else:
            lastBacklog = int(sqlResults[0]["last_backlog"])
            if lastBacklog > datetime.date.today().toordinal():
                lastBacklog = 1

        self._lastBacklog = lastBacklog
        return self._lastBacklog

    def _get_segments(self, show, fromDate):
        anyQualities, bestQualities = common.Quality.splitQuality(show.quality)  #@UnusedVariable

        logger.log(u"Seeing if we need anything from " + show.name)

        myDB = db.DBConnection()
        if show.air_by_date:
            sqlResults = myDB.select(
                "SELECT ep.status, ep.season, ep.episode FROM tv_episodes ep, tv_shows show WHERE season != 0 AND ep.showid = show.indexer_id AND show.paused = 0 ANd ep.airdate > ? AND ep.showid = ? AND show.air_by_date = 1",
                [fromDate.toordinal(), show.indexerid])
        else:
            sqlResults = myDB.select(
                "SELECT status, season, episode FROM tv_episodes WHERE showid = ? AND season > 0 and airdate > ?",
                [show.indexerid, fromDate.toordinal()])

        # check through the list of statuses to see if we want any
        wanted = {}
        for result in sqlResults:
            curCompositeStatus = int(result["status"])
            curStatus, curQuality = common.Quality.splitCompositeStatus(curCompositeStatus)

            if bestQualities:
                highestBestQuality = max(bestQualities)
            else:
                highestBestQuality = 0

            # if we need a better one then say yes
            if (curStatus in (common.DOWNLOADED, common.SNATCHED, common.SNATCHED_PROPER,
                              common.SNATCHED_BEST) and curQuality < highestBestQuality) or curStatus == common.WANTED:

                epObj = show.getEpisode(int(result["season"]), int(result["episode"]))

                if epObj.season in wanted:
                    wanted[epObj.season].append(epObj)
                else:
                    wanted[epObj.season] = [epObj]

        return wanted

    def _set_lastBacklog(self, when):

        logger.log(u"Setting the last backlog in the DB to " + str(when), logger.DEBUG)

        myDB = db.DBConnection()
        sqlResults = myDB.select("SELECT * FROM info")

        if len(sqlResults) == 0:
            myDB.action("INSERT INTO info (last_backlog, last_indexer) VALUES (?,?)", [str(when), 0])
        else:
            myDB.action("UPDATE info SET last_backlog=" + str(when))


    def run(self):
        try:
            self.searchBacklog()
        except:
            self.amActive = False
            raise
