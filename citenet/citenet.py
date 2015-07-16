# -*- coding: utf-8 -*-
'''
citenet - Citation Network Analyzer
Copyright (C) 2015 Jesse Lecy <jdlecy@gmail.com>, with contributions from
Diego Moreda <diego.plan9@gmail.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License along
with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
'''

import codecs
from datetime import datetime, timedelta
import logging
import os
import pkg_resources
from random import normalvariate, lognormvariate
import sqlite3
import sys
import time

from PySide.QtCore import (
    SIGNAL,
    QFile,
    QObject,
    QSettings,
    QTimer,
    Qt,
)
from PySide.QtGui import (
    QApplication,
    QFileDialog,
    QIntValidator,
    QLabel,
    QMessageBox,
)
from PySide.QtUiTools import QUiLoader
from PySide.QtWebKit import QWebView


logger = logging.getLogger('main')


class QTLogHandler(logging.Handler):
    '''
    Logging handler that outputs to a QTextEdit widget
    '''
    def __init__(self, dest = None):
        logging.Handler.__init__(self)
        self.dest = dest
        self.level = logging.DEBUG

    def flush(self):
        pass

    def emit(self, record):
        try:
            msg = self.format(record)
            self.dest.append(msg)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)


class DBConnection(object):
    '''
    Database connection to a SQLite file
    '''
    def __init__(self, filename):
        self.filename = filename
        self.con = None
        
    def open(self):
        self.con = sqlite3.connect(self.filename)
    
    def close(self):
        if self.con :
            self.con.close()
        self.con = None
        
    def commit(self):
        if self.con :
            self.con.commit()
    
    def rollback(self):
        if self.con :
            self.con.rollback()
            
    def get_cursor(self):
        if self.con :
            return self.con.cursor()
        else :
            logger.warning("Attempting to get cursor, but DB closed")
            self.open()
            return self.con.cursor()


class Citenet(QObject):
    FORMS           = {} # dict of .ui file paths
    TIMEOUT_CAPTCHA = 60*5  # minutes
    TIMEOUT_BLOCK   = 60*6 # minutes
    ATTEMPTS        = 0
    FORCE_DELAY     = False # add artificial delays
    was_paused      = False # detect if the search was paused by the user
    total_records   = 0 # number of records written to disk to far
    to_be_dumped    = [] # articles to be dumped
    
    SIMULATED_CAPTCHA = False
    DONE_CAPTCHA      = False
    SIMULATED_BLOCK   = False
    DONE_BLOCK        = False
    
    def init_status(self):
        self.status_label = QLabel('')
        self.status_label.setStyleSheet("QLabel { color : grey; }")
        self.status_label_prog = QLabel('')
        self.status_label_prog.setStyleSheet("QLabel { color : grey; }")
    
    def change_status(self, text, red=False):
        self.status_label.setText('Status: ' + text)
        self.status_label_prog.setText('Status: ' + text)
        if (red):
            self.status_label.setStyleSheet("QLabel { color : red; }")
            self.status_label_prog.setStyleSheet("QLabel { color : red; }")
        else :
            self.status_label.setStyleSheet("QLabel { color : grey; }")
            self.status_label_prog.setStyleSheet("QLabel { color : grey; }")
            
        app.processEvents()
    
    def load_url(self, url, timeout=30):
        '''
        Load an url, sleeping for a bit if FORCE_DELAY is enabled. A timer is
        used for checking for timeout errors due to network connection, etc
        '''
        previous_status = self.status_label.text()[8:]
        self.change_status('Sleeping before request')
        self.sleep_lognorm()
        self.change_status(previous_status)
        
        # launch the timer
        if self.error_timer.isActive():
            self.error_timer.stop()
        self.error_timer.start(timeout*1000)
        self.last_url = url
        logger.info(url)
        self.vw.load(url)
    
    def url_timeout(self):
        self.change_status('Connection error - retrying in 5 minutes', red=True)
        logger.warning('Connection error - retrying in 5 minutes')
        if self.timeout_retry_timer.isActive() :
            self.timeout_retry_timer.stop()
        self.timeout_retry_timer.start(5*60*1000)
    
    def url_retry(self):
        self.change_status('Retrying last url')
        self.load_url(self.last_url)
    
    # timer and blocking related functions
    def create_timer(self):
        '''
        Timer is created as single shot and stopped. It is started each time a
        captcha/block is detected, with the proper interval.
        '''
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.connect(self.timer, SIGNAL("timeout()"), self.timer_wakeup)
        
        # timeout and error timers
        self.error_timer         = QTimer()
        self.error_timer.setSingleShot(True)
        self.connect(self.error_timer, SIGNAL("timeout()"), self.url_timeout)
        self.timeout_retry_timer = QTimer()
        self.timeout_retry_timer.setSingleShot(True)
        self.connect(self.timeout_retry_timer, SIGNAL("timeout()"), self.url_retry)
        
    def timer_wakeup(self):
        # resume search
        logger.info('%s Resuming search ...' % datetime.now())
        self.change_status('Trying to resume search')
        self.do_continue_data_collection()
        
    def detect_captcha(self, page):
        url = page.baseUrl().toString()
        txt = ''
        page_txt = page.toPlainText()
        
        if 'sorry' in url or\
           'but your computer or network may be sending automated queries' in page_txt or\
             not page.findFirstElement("#captcha[name='captcha']").isNull() or\
             '/+/+/+/+/+' in page_txt or\
             'not a robot' in page_txt or\
             not page.findFirstElement("[id*=captcha]").isNull():
            # calculate the extra delay
            delay = 0
            extra_delay = self.ATTEMPTS * 30
            
            # captcha
            if not page.findFirstElement("#captcha[name='captcha']").isNull() or\
               not page.findFirstElement("[id*=captcha]").isNull() :
                logger.warning('Captcha detected')
                
                if self.FORCE_DELAY:
                    extra_delay = extra_delay + normalvariate(0,2)
                delay = self.TIMEOUT_CAPTCHA + extra_delay
                txt = 'Captcha detected'
            
            # block
            elif 'sorry' in url or\
                 'but your computer or network may be sending automated queries' in page_txt :
                logger.warning('Block detected')
                
                if self.FORCE_DELAY:
                    extra_delay = extra_delay + normalvariate(0,10)
                delay = self.TIMEOUT_BLOCK + extra_delay
                txt = 'Block detected'
            
            # 403 forbidden
            elif '/+/+/+/+/+' in page_txt :
                logger.warning('403/Forbidden detected')
                logger.info(page_txt)
                delay = 1
                txt = '403/Forbidden detected'
            
            else :
                # false alarm
                logger.warning('Warning: potential captcha/block found, but not confirmed')
                self.ATTEMPTS = 0
                return False
            
            self.ATTEMPTS = self.ATTEMPTS + 1
            self.timer.start(delay * 60 * 1000)
            txt = txt + ' - sleeping until ' + \
                (datetime.now() + timedelta(seconds=delay*60)).strftime("%m/%d/%y %H:%M")
            logger.warning(txt)
            self.change_status(txt, red=True)
            
            # enable all the +/- buttons
            self.win0.setEnabled(True)
            self.win1.setEnabled(True)
            self.win2.setEnabled(True)
            self.win3.setEnabled(True)
            self.win4.setEnabled(True)
            
            return datetime.now() + timedelta(seconds=delay*60)
        else :
            self.ATTEMPTS = 0
            return False
    
    def sleep_lognorm(self):
        if self.FORCE_DELAY:
            # careful will negatives!
            i = max(lognormvariate(0,1), 0) + 1
            time.sleep(i)
    
    # end timer and blocking related functions
    
    def findNextBracket(self, s, ind):
        nleft = 1
        while ind < len(s):
            if s[ind] == '}':
                nleft -= 1
            elif s[ind] == '{':
                nleft += 1
            if nleft == 0:
                return ind
            ind += 1
            
        return -1
    
    def bibtex2dic(self, t):
        res = dict()
        p = t.find('{')
        res["type"] = t[1:p]
        oldP = p
        p = t.find(",", p)
        res["bibtexkey"] = t[oldP + 1:p]
        while p < len(t):
            oldP = p + 2
            p = t.find("={", p);
            if -1 == p:
                break
            k = t[oldP:p].strip()
            oldP = p + 2
            p = self.findNextBracket(t, oldP)
            if -1 == p:
                break
            res[k] = t[oldP:p]
        return res
    
    def dumpC(self):
        cs = self.vw.page().networkAccessManager().cookieJar().cookiesForUrl(self.vw.url().toString())
        for c in cs:
            print c.name() + ";" + c.value()
    
    def getBitTexUrls(self):
        res = []
        ls = self.vw.page().mainFrame().evaluateJavaScript("var arr= new Array();var elms=document.getElementsByTagName(\"a\");for (var i = 0; i < elms.length; i++){if(\"Import into BibTeX\" == elms[i].innerHTML){arr.push(elms[i].href);}}arr;")
        if not ls is None:
            for l in ls:
                res.append(l)
        return res
    
    def getRelated(self):
        res = []
        ls = self.vw.page().mainFrame().evaluateJavaScript("var arr= new Array();var elms=document.getElementsByClassName(\"gs_fl\");for (var i = 0; i < elms.length; i++){if (elms[i].className == \"gs_fl\"){var elms1=elms[i].getElementsByTagName(\"a\");var s = arr.length;for (var j = 0; j < elms1.length; j++){if(elms1[j].innerHTML.indexOf(\"Related articles\") != -1){arr.push(elms1[j].href); found = 1; break;}}if (s == arr.length) arr.push(\"\");}};arr;")
        if not ls is None:
            for l in ls:
                p = l.find("related:")
                p0 = l.find(":", p + 8)
                if p != -1 and p0 != -1:
                    res.append(l[p + 8: p0])
                else:
                    res.append("")
        return res
    
    def getCitesInfo(self):
        res = []
        citedBy = self.vw.page().mainFrame().evaluateJavaScript("var arr= new Array();var elms=document.getElementsByClassName(\"gs_fl\");for (var i = 0; i < elms.length; i++){if (elms[i].className == \"gs_fl\"){var elms1=elms[i].getElementsByTagName(\"a\");var s = arr.length;for (var j = 0; j < elms1.length; j++){if(elms1[j].innerHTML.indexOf(\"Cited by\") != -1){arr.push(elms1[j].href); arr.push(elms1[j].innerHTML);found = 1; break;}}if (s == arr.length) {arr.push(\"\");arr.push(\"\");};}};arr;")
        
        s = len(citedBy)
        if not citedBy is None:
            for i in xrange(0, s / 2):
                p = citedBy[2 * i + 0].find('?cites=')
                p0 = citedBy[2 * i + 0].find('&', p)
                if p != -1 and p0 != -1:
                    res.append( (citedBy[2 * i + 0][p + 7:p0], citedBy[2 * i + 1][9:]) )
                else:
                    res.append( ("", "0") )
        return res
    
    def evalJS(self):
        js = self.df.edt.toPlainText()
        if len(js) > 0:
            ls = self.vw.page().mainFrame().evaluateJavaScript(js)
            if not ls is None:
                for l in ls:
                    self.df.edtOut.append(l)

    def goto_0_from_3(self):
        self.win0.move(self.win3.x(), self.win3.y())
        self.win3.hide()
        self.win0.statusbar.addWidget(self.status_label, 1)
        self.win0.show()
    
    def go_from_0(self):
        self.win0.move(self.win1.x(), self.win1.y())
        self.win1.hide()
        self.win0.statusbar.addWidget(self.status_label, 1)
        self.win0.show()
        
    def goto0(self):
        # update the delay values
        self.TIMEOUT_CAPTCHA = self.win0.spinCaptcha.value()
        self.TIMEOUT_BLOCK = self.win0.spinBlock.value()
        self.FORCE_DELAY = self.win0.checkDelay.isChecked()
        self.was_paused = False
        
        self.win1.move(self.win0.x(), self.win0.y())
        self.win0.hide()
        self.win1.setEnabled(True)
        self.win1.statusbar.addWidget(self.status_label, 1)
        self.win1.show()
        
    def goto2(self):
        if len(self.win1.edtKeywords.text()) == 0:
            return
        self.q = self.win1.edtKeywords.text()
        self.start = 0
        self.win1.setEnabled(False)
        self.goto_more = True
        self.ss = "stage0"
        if len(self.q) > 0:
            # it was valid before recent changes
            #self.load_url("http://scholar.google.com/scholar?q=" + self.q + "&btnG=&hl=en&as_sdt=0,5")
            #self.load_url("http://scholar.google.com/scholar_settings?hl=en&as_sdt=0,5")
            self.change_status('Loading top level page')
            self.load_url("http://scholar.google.com/ncr")
    
    def goto1(self):
        #self.win2.lstArticles.clear()
        self.win2.lstCandidates.clear()
        self.win1.move(self.win2.x(), self.win2.y())
        self.win2.hide()
        self.win1.setEnabled(True)
        self.win1.statusbar.addWidget(self.status_label, 1)
        self.win1.show()
        
    def prev_page_from_3(self):
        self.win3.listWidget.clear()
        self.win2.move(self.win3.x(), self.win3.y())
        self.win3.hide()
        self.win2.statusbar.addWidget(self.status_label, 1)
        self.win2.show()
    
    def goto3(self):
        
        count = self.win2.lstArticles.count()
        if 0 == count:
            return
        self.win3.move(self.win2.x(), self.win2.y())
        self.win2.hide()
        self.seedPapers = []
        for i in xrange(0, count):
            item = self.win2.lstArticles.item(i)
            self.win3.listWidget.addItem(item.text())
            self.seedPapers.append(self.lpDicts[item.text()])
        self.win3.statusbar.addWidget(self.status_label, 1)
        self.win3.show()
    
    def dogoto2(self):
        
        self.add_more_results()
        self.win2.move(self.win1.x(), self.win1.y())
        self.win1.hide()
        self.win1.setEnabled(True)
        self.win2.statusbar.addWidget(self.status_label, 1)
        self.win2.show()
    
    def quote_identifier(self, s, errors="strict"):
        encodable = s.encode("utf-8", errors).decode("utf-8")
        nul_index = encodable.find("\x00")
        if nul_index >= 0:
            error = UnicodeEncodeError("NUL-terminated utf-8", encodable, nul_index, nul_index + 1, "NUL not allowed")
            error_handler = codecs.lookup_error(errors)
            replacement, _ = error_handler(error)
            encodable = encodable.replace("\x00", replacement)
        
        return encodable.replace("\"", "\"\"")
    
    def get_existing_pub_id(self, bib, title, author):
        cur = self.dbcon.get_cursor()
        pubid = ""
        
        try:
            # try (bibtexkey, title)
            q = 'select pubid from publications where bibtexkey = \"%s\" and title = \"%s\";' % (bib, title)
            cur.execute(q)
            r = cur.fetchone()
            
            # try (bibtexkey, title-without-quotes)
            if r is None or len(r) == 0 :
                q = 'select pubid from publications where bibtexkey = \"%s\" and title = \"%s\";' % (bib, title.replace("''", "'"))
                cur.execute(q)
                r = cur.fetchone()
            
            # try (bibtexkey, author)
            if r is None or len(r) == 0 :
                q = 'select pubid from publications where bibtexkey = \"%s\" and author = \"%s\";' % (bib, author)
                cur.execute(q)
                r = cur.fetchone()
            
            # publication was found
            if not r is None and len(r) > 0 :
                pubid = r[0]
            
        except sqlite3.Error, e:
            logger.error("002: DB error %s:" % e.args[0])
            logger.exception(e)
            exit(-1)
        
        return pubid
        
    def save_publication(self, pub):
        cur = self.dbcon.get_cursor()
        pub["searchlevel"] = str(self.current_level)
        
        for k, v in pub.items():
            v = v.replace("'", "''")
            pub[k] = self.quote_identifier(v)
            continue
        
        fix = dict()
        fix["number"] = "num"
        
        for k, v in pub.items():
            if k in fix:
                del pub[k]
                pub[fix[k]] = v
        
        good = ['bibtexkey', 'type', 'title', 'author', 'journal', 'volume', 'num', 'pages', 'year', 'publisher', 'cites', 'citedby', 'related', 'searchlevel']
        
        for k, v in pub.items():
            if not k in good:
                del pub[k]
        
        if pub["bibtexkey"] == 'gardner2010scholarly' :
            print "x"
        
        new_pub = False
        pubid = self.get_existing_pub_id(pub["bibtexkey"], pub["title"], pub["author"])
        # publication was not found
        if len(pubid) == 0:
            pubid = pub['bibtexkey'] + ('_%010d' % (self.total_records))
            new_pub = True
        
        pub["pubid"] = pubid
        
        try:
            # add to publications
            if (new_pub) :
                q = 'insert into publications(%s) values(%s);' % (",".join(pub.keys()), "'" + "','".join(pub.values()) + "'")
                cur.execute(q)

            # add to citationrelationship
            if self.current_level > 0:
                q = 'insert into citationrelationship(Citation_ID, Publication_ID) values("%s", "%s");' % (pubid, self.parent_bibtex)
                #print (q)
                cur.execute(q)
            
            # commit
            self.dbcon.commit()
            # increase record count
            if (new_pub) :
                self.total_records += 1
        except sqlite3.Error, e:
            logger.error(e)
            logger.exception(e)
            
            # roll back the transaction
            self.dbcon.rollback()
            
            return False
            
        return True
    
    def create_db(self, path):
        try:
            cur = self.dbcon.get_cursor()
            cur.execute('drop table if exists header;')
            cur.execute('drop table if exists citationrelationship;')
            cur.execute('drop table if exists publications;')
            cur.execute('create table header(key varchar(64), value varchar(64));')
            cur.execute('create table CitationRelationship(Citation_ID text, Publication_ID text, primary key (Citation_ID, Publication_ID));')
            #cur.execute('create table Publications(bibtexkey varchar(64), type varchar(16), title varchar(256), author varchar(128), journal varchar(64), booktitle varchar(64), volume varchar(16), number varchar(16), pages varchar(16), year varchar(16), publisher varchar(64), organization varchar(32), institution varchar(64), school varchar(64), cites varchar(32), citedby varchar(16), searchlevel varchar(16));')
            cur.execute('create table Publications (BibtexKey text, PubID text, Type text, Title text, Author text, Journal text, Volume integer, Num integer, Pages text, Year integer, Publisher text, Cites text, CitedBy integer, Related text, SearchLevel integer, primary key (BibtexKey, Title))')
            
            self.current_level = 0
            header = dict()
            header["query"] = self.quote_identifier(self.q)
            header["ppl"] = self.win3.edtPercentPerLevel.text()
            header["max_number"] = self.win3.edtPercentPerLevel.text()
            header["maxpl"] = self.win3.edtMaxPerLevel.text()
            header["max_level"] = str(int(self.win3.edtMaxLevel.text()) + 1)
            header["current_level"] = "1"
            header["current_row"] = "0"
            header["progress"] = "0"
            header["scrape_done"] = "0"
            header["level_limit"] = str(len(self.seedPapers))
            if self.win3.rbtnPercent.isChecked():
                header["use_percent"] = "1"
            else:
                header["use_percent"] = "0"
                
            for p in self.seedPapers:
                try :
                    self.save_publication(p)
                except Exception as e:
                    logger.error('%s Error saving publication "%s"' % (datetime.now(), p))
                    logger.exception(e)

            
            for k, v in header.items():
                q = 'insert into header(key, value) values("%s",  "%s");' % (str(k), str(v))
                cur.execute(q)
            self.dbcon.commit()
            self.current_level = 1
            self.scrape_done = False
            self.was_paused = False
        
        except sqlite3.Error, e:
            logger.error("1: DB error %s:" % e.args[0])
            logger.exception(e)
            return False
            
        return True
    
    def continue_data_collection(self):
        cur = self.dbcon.get_cursor()
        
        try:
            cur.execute("SELECT value FROM header WHERE key = 'ppl'")
            self.ppl = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'max_level'")
            self.max_level = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'current_level'")
            self.current_level = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'current_row'")
            self.current_row = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'progress'")
            self.progress = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'use_percent'")
            self.use_percent = int(cur.fetchone()[0]) == 1
            cur.execute("SELECT value FROM header WHERE key = 'maxpl'")
            self.maxpl = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'level_limit'")
            self.level_limit = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM header WHERE key = 'scrape_done'")
            self.scrape_done = int(cur.fetchone()[0]) == 1
        
        except sqlite3.Error, e:
            logger.error("1: DB error %s:" % e.args[0])
            logger.exception(e)
            return
        
        if self.scrape_done:
            self.win4.hide()
            QMessageBox.information(self.win1, "Already done", "Great luck. Scrape for this query is already finished!")
            self.win0.setEnabled(True)
            return
        self.do_continue_data_collection()
    
    def begin_data_collection(self):
        # check for duplicate file
        if QFile.exists(self.win3.edtDBname.text()):
            r = QMessageBox.question(self.win3, "File already exists", "File already exists. Are you sure you want to continue? All information in the file will be lost.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r == QMessageBox.No:
                return
        
        # initialize database
        self.dbcon = DBConnection(self.win3.edtDBname.text())
        self.dbcon.open()
        if not self.create_db(self.dbcon.filename) :
            QMessageBox.critical(self.win3, "Error", "Can not create db file.")
            self.dbcon = None
            return
        
        self.win3.setEnabled(False)
        self.from1 = False
        self.win4.show()
        self.win4.lblPaper.setText("")
        self.continue_data_collection()
    
    def dump_scrape_progress(self):
        cur = self.dbcon.get_cursor()
        header = dict()
        
        header["current_row"]   = str(self.current_row)
        header["current_level"] = str(self.current_level)
        header["progress"]      = str(self.progress)
        header["level_limit"]   = str(self.level_limit)
        if self.scrape_done:
            header["scrape_done"] = "1"
        else:
            header["scrape_done"] = "0"
        
        for k, v in header.items():
            q = "update header set value = '%s' where key = '%s';" % (str(v), str(k))
            cur.execute(q)
        
        self.dbcon.commit()
    
    def stop_scrape(self):
        self.change_status('Search stopped manually')
        self.was_paused = True
        self.dbcon.close()
        
        # return to original state
        self.win3.setEnabled(True)
        self.win3.listWidget.clear()
        self.win2.lstArticles.clear()
        self.win2.lstCandidates.clear()
        self.win4.close()
        self.win3.close()
        
        self.win0.setEnabled(True)
        self.goto_0_from_3()
    
    def do_resume_search(self):
        if self.working or self.win1.isVisible() or self.win2.isVisible():
            return
        try:
            if not self.dbcon is None:
                self.dbcon.commit()
                self.dbcon.close()
            self.dbcon = DBConnection(self.sdb)
            self.dbcon.open()
            
            # count the number of articles already on DB
            cur = self.dbcon.get_cursor()
            cur.execute('select count(rowid) from publications;')
            self.total_records = int(cur.fetchone()[0])
            logger.info(self.total_records)
        
        except sqlite3.Error, _:
            QMessageBox.critical(self.win1, "Error", "Invalid db file, can not resume search")
            return
        
        settings = QSettings("Software Kernels", "Scholar")
        settings.setValue("lastdb", self.sdb)
        
        self.goto_more = False
        self.ss = "stage0"
        self.change_status('Resuming search')
        self.load_url("http://scholar.google.com/ncr")
    
    def resume_search(self):
        # update the delay values
        self.TIMEOUT_CAPTCHA = self.win0.spinCaptcha.value()
        self.TIMEOUT_BLOCK = self.win0.spinBlock.value()
        self.FORCE_DELAY = self.win0.checkDelay.isChecked()
        
        self.sdb = QFileDialog.getOpenFileName(self.win1, "Select db", "Select db to continue the search")[0]
        if self.sdb is None or len(self.sdb) == 0:
            return
        self.working = False
        self.do_resume_search()
    
    def update_progress(self):
        '''
        Update the labels and progress on the Progress dialog
        '''
        if hasattr(self, 'current_level') and hasattr(self, 'max_level') and\
                hasattr(self, 'current_row') and hasattr(self, 'level_limit') and\
                hasattr(self, 'lpCurr') and hasattr(self, 'current_max_progress') :
            try :
                progress = 0
                if self.progress:
                    progress = self.progress
                t_level  = "%d of %d" % (self.current_level, self.max_level - 1)
                t_parent = "%d of %d" % (self.current_row + 1, self.level_limit)
                t_article = "%d of %d" % (self.lpCurr + progress, self.current_max_progress)
                
                self.win4.lblLevel.setText(t_level)
                self.win4.lblParent.setText(t_parent)
                self.win4.lblArticle.setText(t_article)
                
                # progress bar
                p_level  = (self.current_level - 1) / float(self.max_level - 1)
                p_parent = (self.current_row) / float(self.level_limit)
                p_progress = (self.lpCurr + progress) / float(self.current_max_progress)
                total = int(p_level*100. + p_parent*100./(self.max_level -1) + p_progress*100.*(1/float(self.max_level - 1))*(1/float(self.level_limit)))
                
                self.win4.progress.setValue(total)
                self.win4.progress_2.setValue(int(p_progress*100))
                
                app.processEvents()
            except Exception as e:
                logger.warning("Warning: progress could not be updated")
                logger.exception(e)
    
    def dump_papers_to_db(self):
        '''
        Dump the pending articles to the database
        '''
        try:
            # update status and force redraw
            self.change_status('Writing publications into the database')
            logger.info("Writing %i articles into the DB" % len(self.to_be_dumped))
            app.processEvents()
            
            for d in self.to_be_dumped:
                try :
                    self.save_publication(d)
                except Exception as e:
                    logger.error('%s Error saving publication "%s"' % (datetime.now(), d))
                    logger.error(d)
                    logger.exception(e)

            self.dump_scrape_progress()
            
        except sqlite3.Error, e:
            logger.error("2: DB error %s:" % e.args[0])
            logger.exception(e)
            return
    
    def dump_papers(self):
        try:
            self.dbcon.open()
            
            # update status and force redraw
            self.change_status('Adding publications to the DB queue')
            app.processEvents()

            cur = self.dbcon.get_cursor()
            q = 'SELECT citedby FROM publications WHERE rowid = %s' % (str(self.current_row + 1))
            cur.execute(q)
            
            if self.use_percent:
                r = cur.fetchone()
                max_progress = int((int(r[0]) * self.ppl) / 100)
                #cite at least one article
                if max_progress == 0 and r[0] > 0:
                    max_progress = 1
            else:
                max_progress = self.maxpl
            
            i = 0
            for p in self.lpPapers:
                try :
                    d = self.bibtex2dic(p)
                    d["cites"] = self.lpCites[i][0]
                    d["citedby"] = self.lpCites[i][1]
                    d["related"] = self.lpRelated[i]
                    self.to_be_dumped += [d]
                    
                except Exception as e:
                    logger.error('%s Error queuing publication "%s"' % (datetime.now(), d))
                    logger.error(d)
                    logger.exception(e)
                
                self.progress += 1
                i += 1
                if self.progress >= max_progress:
                    break
            
            # all the articles for this paper have been retrieved
            if self.progress >= max_progress or (i < 10 and self.progress < max_progress):
                # dump the publications to the db
                self.dump_papers_to_db()
                self.to_be_dumped = []
                
                self.progress = 0
                self.current_row += 1
                if self.current_row == self.level_limit:
                    cur.execute('select count(rowid) from publications;')
                    self.level_limit = int(cur.fetchone()[0])
                    self.current_level += 1
                    
            self.scrape_done = self.current_level == self.max_level
            self.dbcon.close()
            
        except sqlite3.Error, e:
            logger.error("2: DB error %s:" % e.args[0])
            logger.exception(e)
            return
        
        if self.scrape_done:
            # return to initial dialog when search is completed
            self.win4.hide()
            QMessageBox.information(self.win3, "Great!", "DB is created")
            self.win3.hide()
            self.win0.setEnabled(True)
            self.goto_0_from_3()
        else:
            if     self.was_paused:
                self.was_paused = False
            else:
                self.do_continue_data_collection()
    
    def get_short_desc(self, d):
        l = []
        lr = ["title", "author", "year"]
        for e in lr:
            if e in d:
                l.append(d[e])
        return ", ".join(l)
    
    def add_more_results(self):
        i = 0
        for p in self.lpPapers:
            d = self.bibtex2dic(p)
            n = self.get_short_desc(d)
            n = "%s, cited %s times" % (n, self.lpCites[i][1])
            self.win2.lstCandidates.addItem(n)
            self.lpDicts[n] = d
            d["cites"] = self.lpCites[i][0]
            d["citedby"] = self.lpCites[i][1]
            d["related"] = self.lpRelated[i]
            i += 1
    
    def add_article(self):
        s = self.win2.lstCandidates.selectedItems()
        if len(s) == 0:
            return
        ss = self.win2.lstArticles.findItems(s[0].text(), Qt.MatchExactly)
        if len(ss) > 0:
            return
        self.win2.lstArticles.addItem(s[0].text())
        self.win2.lstCandidates.row(s[0])
    
    def remove_article(self):
        s = self.win2.lstArticles.selectedItems()
        if len(s) == 0:
            return
        r = self.win2.lstArticles.row(s[0])
        self.win2.lstArticles.takeItem(r)
    
    def mrcd(self):
        # calculate max_progress the same way is done inside dump_papers
        try:
            self.dbcon.open()
            cur = self.dbcon.get_cursor()
            q = 'SELECT citedby FROM publications WHERE rowid = %s' % (str(self.current_row + 1))
            cur.execute(q)
            if self.use_percent:
                r = cur.fetchone()
                max_progress = int((int(r[0]) * self.ppl) / 100)
                #cite at least one article
                if max_progress == 0 and r[0] > 0:
                    max_progress = 1
            else:
                max_progress = self.maxpl
            self.dbcon.close()
        except sqlite3.Error, e:
            logger.error("2: DB error %s:" % e.args[0])
            logger.exception(e)
            return

        self.current_max_progress = max_progress
        self.loadPapers(self.dump_papers)
    
    def do_continue_data_collection(self):
        self.dbcon.open()
        cur = self.dbcon.get_cursor()
        cur.execute('SELECT cites, bibtexkey, title, author FROM publications WHERE rowid = %s' % (str(self.current_row + 1)))
        r = cur.fetchone()
        self.citeid = r[0]
        self.parent_bibtex = self.get_existing_pub_id(r[1], r[2], r[3])
        self.dbcon.close()
        
        self.ss = "next"
        self.doNext = self.mrcd
        url = "http://scholar.google.com/scholar?cites=%s&as_sdt=2005&sciodt=0,5&num=100&hl=en&start=%s" %(self.citeid, self.progress)
        self.change_status('Continuing data collection')
        self.load_url(url)
    
    def mrmp(self):
        self.current_max_progress = 0
        self.loadPapers(self.add_more_results)
    
    def more_results(self):
        self.ss = "next"
        self.doNext = self.mrmp
        self.start += 10
        self.load_url("http://scholar.google.com/scholar?q=" + self.q + "&btnG=&hl=en&as_sdt=0,5&start=" + str(self.start))
    
    def loadPapers(self, end):
        self.lpList = self.getBitTexUrls()
        self.lpCites = self.getCitesInfo()
        self.lpRelated = self.getRelated()

        self.lpCurr = 0
        self.lpEnd = end
        self.lpPapers = []
        
        # limit the list of results, discarding those over the limit
        progress = 0
        if hasattr(self, 'progress') :
            progress = self.progress
        
        if self.current_max_progress > 0:
            self.lpList = self.lpList[:self.current_max_progress-progress+1]
            self.lpCites = self.lpCites[:self.current_max_progress-progress+1]
            self.lpRelated = self.lpRelated[:self.current_max_progress-progress+1]

        self.lpOrigURL = self.vw.url().toString()
        if len(self.lpList) == 0:
            self.lpEnd()
        else:
            self.ss = "load_papers"
            self.load_url(self.lpList[0])
    
    def loadFinished(self, ok):
        # stop search altogether on user interruption
        if self.was_paused:
            self.working = False
            self.timeout_retry_timer.stop()
            self.error_timer.stop()
            return
        
        # retry if a timeout/network error was detected
        if not ok:
            if self.timeout_retry_timer.isActive():
                self.timeout_retry_timer.stop()
            
            self.url_timeout()
            return
        
        # check the timeout timers
        if self.timeout_retry_timer.isActive():
            self.timeout_retry_timer.stop()
        if self.error_timer.isActive():
            self.error_timer.stop()
        
        # stop timer if the user manually acts while on captcha/block
        if self.timer.isActive() :
            self.timer.stop()
            self.change_status('Search resumed manually')
        
        # detect captcha/block
        invalid_request = self.detect_captcha(self.vw.page().mainFrame())
        
        if invalid_request :
            self.working = False
            
            # dump the pending publications to the db
            if self.to_be_dumped :
                previous_status = self.status_label.text()[8:]
                self.dbcon.open()
                self.dump_papers_to_db()
                self.to_be_dumped = []
                self.dbcon.close()
                self.change_status(previous_status, True)
            
            # clear cookies
            #if 'scholar_settings' in self.vw.page().mainFrame().baseUrl().toString():
            #    self.vw.page().networkAccessManager().cookieJar().setAllCookies([])
            #    self.ss = 'stage0'
            return
        
        self.vw.hide()
        self.working = True
        if ok:
            if self.ss == "stage0":
                self.change_status('Retrieving candidate seed articles')
                """self.vw.page().mainFrame().evaluateJavaScript("\n\
                var elms=document.getElementsByTagName(\"button\");\n\
                for (var i = 0; i < elms.length; i++)\n\
                {\n\
                   if(\"Settings\" == elms[i].getAttribute(\"aria-label\"))\n\
                {\n\
                elms[i].click();\n\
                break;\n\
                }\n\
                }")
                self.ss = "stage0"""
                self.ss = "stage1"
                self.load_url("http://scholar.google.com/scholar_settings?hl=en&as_sdt=0,5")
            elif self.ss == "stage1":
                self.change_status('Retrieving seed articles')
                self.vw.page().mainFrame().evaluateJavaScript("var ch=document.getElementById(\"scis1\");ch.checked=true;")
                self.vw.page().mainFrame().evaluateJavaScript("var e=document.getElementsByTagName(\"button\");for (var i = 0; i<e.length;i++){if ((e[i].getAttribute(\"class\").indexOf(\"gs_btn_act\") != -1) && (e[i].getAttribute(\"name\") == \"save\")){e[i].click();break;}}")
                self.ss = "stage2"
            elif self.ss == "stage2":
                self.change_status('Collecting data')
                if not self.goto_more:
                    # continue resume search
                    self.win0.setEnabled(False)
                    self.from1 = True
                    self.win4.show()
                    self.win4.lblPaper.setText("")
                    self.was_paused = False
                    self.continue_data_collection()
                else:
                    self.ss = "stage3"
                    self.load_url("http://scholar.google.com/scholar?q=" + self.q + "&btnG=&hl=en&as_sdt=0,5")
                    
            elif self.ss == "stage3":
                self.current_max_progress = 0
                self.loadPapers(self.dogoto2)
            elif self.ss == "load_papers":
                self.lpPapers.append(self.vw.page().mainFrame().toPlainText())
                if len(self.lpPapers):
                    self.win4.lblPaper.setText(self.get_short_desc(self.bibtex2dic(self.lpPapers[-1])))
                    self.update_progress()
                self.lpCurr += 1
                #print self.lpCurr
                if self.lpCurr == len(self.lpList) :# or \
                    #(self.current_max_progress > 0 and self.lpCurr > self.current_max_progress):
                    self.ss = "load_orig"
                    self.load_url(self.lpOrigURL)
                else :
                    self.load_url(self.lpList[self.lpCurr])
            elif self.ss == "load_orig":
                self.lpEnd()
            elif self.ss == "next":
                self.doNext()
    
    def loadProgress(self, progress):
        #print "loadProgress " + str(progress)
        pass
    
    def toggle_web(self):
        self.vw.setVisible(not self.vw.isVisible())
    
    def toggle_log(self):
        self.winlog.setVisible(not self.winlog.isVisible())
    
    def copy_log_clipboard(self):
        QApplication.clipboard().setText(self.winlog.txtLog.toPlainText())
    
    def init_log_window(self):
        # load the UI for the log window
        loader = QUiLoader()
        uifile = QFile(self.FORMS['logwindow.ui'])
        uifile.open(QFile.ReadOnly)
        self.winlog = loader.load(uifile, None)
        uifile.close()
        
        # connect slots
        self.winlog.btnCopy.clicked.connect(self.copy_log_clipboard)
        self.winlog.btnHide.clicked.connect(self.toggle_log)
        #self.winlog.show()
        
        # init logging system
        qt_handler = QTLogHandler(self.winlog.txtLog)
        logger.addHandler(qt_handler)
        
        stderr_log_handler = logging.StreamHandler()
        logger.addHandler(stderr_log_handler)
        
        # nice output format
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        qt_handler.setFormatter(formatter)
        stderr_log_handler.setFormatter(formatter)
        logger.setLevel(logging.INFO)
    
    def __init__(self):
        
        QObject.__init__(self)
        loader = QUiLoader()
        
        # init forms
        for filename in pkg_resources.resource_listdir('citenet', 'resources') :
            self.FORMS[filename] = pkg_resources.resource_filename('citenet',
                                       os.path.join('resources', filename))

        uifile = QFile(self.FORMS['form0.ui'])
        uifile.open(QFile.ReadOnly)
        self.win0 = loader.load(uifile, None)
        uifile.close()
        uifile = QFile(self.FORMS['form1.ui'])
        uifile.open(QFile.ReadOnly)
        self.win1 = loader.load(uifile, None)
        uifile.close()
        uifile = QFile(self.FORMS['form2.ui'])
        uifile.open(QFile.ReadOnly)
        self.win2 = loader.load(uifile, None)
        uifile.close()
        uifile = QFile(self.FORMS['form3.ui'])
        uifile.open(QFile.ReadOnly)
        self.win3 = loader.load(uifile, None)
        uifile.close()
        uifile = QFile(self.FORMS['progress.ui'])
        uifile.open(QFile.ReadOnly)
        self.win4 = loader.load(uifile, None)
        uifile.close()
        iv = QIntValidator(1, 10000000, self)
        self.win1.edtNumber.setValidator(iv)
        iv = QIntValidator(1, 100, self)
        self.win0.btnVis.clicked.connect(self.toggle_web)
        self.win1.btnVis.clicked.connect(self.toggle_web)
        self.win2.btnVis.clicked.connect(self.toggle_web)
        self.win3.btnVis.clicked.connect(self.toggle_web)
        self.win4.btnVis.clicked.connect(self.toggle_web)
        self.win0.btnLog.clicked.connect(self.toggle_log)
        self.win1.btnLog.clicked.connect(self.toggle_log)
        self.win2.btnLog.clicked.connect(self.toggle_log)
        self.win3.btnLog.clicked.connect(self.toggle_log)
        self.win4.btnLog.clicked.connect(self.toggle_log)
        self.win3.edtPercentPerLevel.setValidator(iv)
        self.win3.edtPercentPerLevel.setText("3")
        self.win3.edtMaxPerLevel.setValidator(iv)
        self.win3.edtMaxPerLevel.setText("3")
        self.win3.edtMaxLevel.setText("3")
        self.win3.edtDBname.setText("result.sqlite")
        self.win3.edtMaxLevel.setValidator(iv)
        self.win1.btnNextStep.clicked.connect(self.goto2)
        self.win1.btnResume.clicked.connect(self.go_from_0)
        self.win0.btnResume.clicked.connect(self.resume_search)
        self.vw = QWebView()
        self.vw.loadFinished.connect(self.loadFinished)
        self.vw.loadProgress.connect(self.loadProgress)
        self.win0.btnNewSearch.clicked.connect(self.goto0)
        self.win2.btnAdd.clicked.connect(self.add_article)
        self.win2.btnRemove.clicked.connect(self.remove_article)
        self.win2.btnNewKeywords.clicked.connect(self.goto1)
        self.win2.btnMoreResults.clicked.connect(self.more_results)
        self.win2.btnNextStep.clicked.connect(self.goto3)
        self.win3.btnBegin.clicked.connect(self.begin_data_collection)
        self.win3.btnPrev.clicked.connect(self.prev_page_from_3)
        self.win3.btnCancel.clicked.connect(self.goto_0_from_3)
        self.win4.btnStopScrape.clicked.connect(self.stop_scrape)
        self.win4.setWindowFlags(Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        
        self.init_log_window()
        
        #if False:
        #    file = QFile("form_d.ui")
        #    file.open(QFile.ReadOnly)
        #    self.df = loader.load(file, None)
        #    file.close()
        #    self.df.btnDump.clicked.connect(self.dumpC)
        #    self.df.btnDo.clicked.connect(self.evalJS)
        self.lpDicts = dict()
        self.current_level = 0
        self.from1 = False
        self.win0.show()
        
        # timer
        self.create_timer()
        # default values
        if "-forcedelay" in sys.argv:
            logger.info('Delay enabled')
            self.FORCE_DELAY = True
        
        self.win0.spinCaptcha.setValue(self.TIMEOUT_CAPTCHA)
        self.win0.spinBlock.setValue(self.TIMEOUT_BLOCK)
        self.win0.checkDelay.setChecked(self.FORCE_DELAY)
    
        res = None
        
        # add status bar widget
        self.init_status()
        self.win0.statusbar.addWidget(self.status_label, 1)
        self.win4.statusbar.addWidget(self.status_label_prog, 1)
        self.change_status('Idle')
        
        # change to home directory (My Documents)
        os.chdir(os.path.expanduser("~"))
        
        # database
        self.dbcon = None
        
        if len(sys.argv) > 1:
            if sys.argv[1] == "-resumelast":
                settings = QSettings("Software Kernels", "Scholar")
                res = settings.value("lastdb")
                
        if len(sys.argv) > 2:
            if sys.argv[1] == "-resume":
                res = sys.argv[2]
            

            
        if not res is None and len(res) > 0:
            self.sdb = res
            self.working = False
            self.do_resume_search()
            
        
if __name__=="__main__":
    app = QApplication(sys.argv)
    s = Citenet()
    r = app.exec_()
    if s.dbcon is not None:
        s.dbcon.commit()
        s.dbcon.close()
    sys.exit(r)
