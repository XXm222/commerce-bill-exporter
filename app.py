"""PyQt6 桌面界面。浏览器自动化仅调用系统 Google Chrome。"""
import argparse, datetime as dt, json, os, queue, sys, threading
from pathlib import Path
from PyQt6.QtCore import Qt, QDate, QTimer, QLocale, QUrl, QLockFile
from PyQt6.QtGui import QFont, QDesktopServices, QColor
from PyQt6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QCheckBox,QLineEdit,QDateEdit,QListWidget,QListWidgetItem,QDialog,QFormLayout,QFileDialog,QMessageBox,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QStackedWidget,QProgressBar,QMenu,QSpinBox,QComboBox,QCalendarWidget)
from accounts import Store, atomic, crypt, load_json, STARTUP_PROBLEMS
import environment
from engine import Engine, PLATFORMS, validate_dates, export_period, next_same_platform, BASE, readable_error

VERSION='0.6.1'
DATA=(Path.home()/'Library/Application Support/CommerceBillExport/Desktop' if sys.platform=='darwin' else Path(os.environ.get('LOCALAPPDATA',Path.home()))/'CommerceBillExport')
CHECKED=Qt.CheckState.Checked;UNCHECKED=Qt.CheckState.Unchecked;USER=Qt.ItemDataRole.UserRole
STYLE='''
QMainWindow, QDialog { background:#F5F7FA; }
QWidget { color:#243247; font-size:13px; }
QLabel#Title { font-size:27px; font-weight:650; color:#17263B; }
QLabel#Section { font-size:16px; font-weight:600; color:#203047; }
QLabel#Muted { color:#617086; }
QLabel#EmptyTitle { font-size:16px; font-weight:600; color:#42536C; }
QFrame#Panel { background:white; border:1px solid #DFE5ED; border-radius:10px; }
QLineEdit, QDateEdit { background:white; border:1px solid #CCD5E1; border-radius:6px; padding:9px 11px; selection-background-color:#D8E7FF; color:#23334B; }
QLineEdit:focus, QDateEdit:focus { border:1px solid #326CC5; }
QLineEdit:disabled, QDateEdit:disabled { background:#F1F4F8; color:#748298; }
QDateEdit { font-size:14px; padding:8px 12px; }
QPushButton { background:white; border:1px solid #CDD6E1; border-radius:6px; padding:8px 13px; font-weight:500; }
QPushButton:hover { background:#F0F5FD; border-color:#92AFD8; }
QPushButton:pressed { background:#E4EDFB; }
QPushButton:focus { border:1px solid #326CC5; }
QPushButton:disabled { color:#8190A4; border-color:#E0E5EC; background:#F2F4F7; }
QPushButton#Primary { background:#285FB3; color:white; border:1px solid #285FB3; padding:11px 26px; font-size:14px; font-weight:600; }
QPushButton#Primary:hover { background:#1E4F9A; }
QPushButton#Primary:pressed { background:#194481; }
QPushButton#Primary:disabled { background:#9BAECC; border-color:#9BAECC; color:white; }
QPushButton#Quiet { background:transparent; border-color:transparent; color:#52657F; }
QPushButton#Quiet:hover { background:#EAF0F9; }
QPushButton#Quiet:disabled { color:#8A98AB; background:transparent; border-color:transparent; }
QFrame#PlatformRow { background:transparent; border-radius:6px; }
QFrame#PlatformRow[selected="true"] { background:#EAF1FC; }
QPushButton#PlatformName { background:transparent; border:none; text-align:left; padding:10px 8px; font-size:16px; font-weight:600; }
QPushButton#PlatformName[selected="true"] { color:#285FB3; }
QPushButton#PlatformName:focus { border:1px solid #326CC5; }
QTabBar::tab { background:transparent; color:#66768E; padding:10px 20px; border-bottom:2px solid transparent; font-weight:500; }
QTabBar::tab:selected { color:#285FB3; border-bottom:2px solid #285FB3; }
QTabBar::tab:hover { color:#285FB3; background:#F4F7FC; }
QCheckBox { spacing:8px; }
QCheckBox::indicator { width:16px; height:16px; }
QListWidget { background:white; border:none; outline:none; }
QListWidget::item { padding:10px 8px; border-radius:5px; margin:2px 0; }
QListWidget::item:selected { background:#EAF1FC; color:#234E88; }
QListWidget::item:hover { background:#F2F6FC; }
QTableWidget { border:none; background:white; gridline-color:#EEF1F5; selection-background-color:#EAF1FC; selection-color:#223E65; outline:none; }
QHeaderView::section { background:#F6F8FB; color:#596B83; border:none; border-bottom:1px solid #E4E9F1; padding:10px; font-weight:500; }
QScrollBar:vertical { background:#F4F6F9; width:8px; margin:2px; }
QScrollBar::handle:vertical { background:#BBC8D8; border-radius:3px; min-height:28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QProgressBar { border:none; background:#E4EBF5; border-radius:2px; max-height:4px; }
QProgressBar::chunk { background:#326CC5; border-radius:2px; }
QDialog QProgressBar { min-height:7px; max-height:7px; border-radius:3px; }
QMenu { background:white; border:1px solid #DCE3ED; padding:5px; }
QMenu::item { padding:8px 20px; }
QMenu::item:selected { background:#EAF1FC; }
QCalendarWidget QAbstractItemView { background:white; selection-background-color:#285FB3; selection-color:white; }
QFrame#Notice { background:#FFF6E5; border:1px solid #EBD6A8; border-radius:8px; }
QLabel#NoticeTitle { font-size:14px; font-weight:600; color:#7A5410; }
QFrame#Notice QPushButton { background:white; border:1px solid #DCC79A; color:#6B4A0E; }
QFrame#Notice QPushButton:hover { background:#FFFBF2; border-color:#C9AF7A; }
'''

def label(text,style=None):
    w=QLabel(text)
    if style:w.setObjectName(style)
    return w

def button(text,callback,style=None):
    w=QPushButton(text);w.clicked.connect(callback)
    if style:w.setObjectName(style)
    return w

def panel():
    w=QFrame();w.setObjectName('Panel');layout=QVBoxLayout(w);layout.setContentsMargins(18,16,18,16);layout.setSpacing(12);return w,layout

def empty(title,text):
    w=QWidget();v=QVBoxLayout(w);v.setContentsMargins(20,10,20,10);v.setSpacing(8);v.addStretch()
    for value,style in [(title,'EmptyTitle'),(text,'Muted')]:
        a=label(value,style);a.setAlignment(Qt.AlignmentFlag.AlignCenter);a.setWordWrap(True);v.addWidget(a)
    v.addStretch();return w

class MonthRangeDialog(QDialog):
    """整月范围选择器：年份可直接输入，月份可直接从列表选择。"""
    def __init__(self,start,end,parent=None):
        super().__init__(parent);self.setWindowTitle('按月份选择');self.setMinimumWidth(420)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,22,24,22);layout.setSpacing(16)
        intro=label('选择开始和结束月份，自动填入月初至月末。','Muted');intro.setWordWrap(True);layout.addWidget(intro)
        self.fields=[]
        for title,date in [('开始月份',start),('结束月份',end)]:
            line=QHBoxLayout();line.setSpacing(10);caption=label(title);caption.setMinimumWidth(72);line.addWidget(caption)
            year=QSpinBox();year.setRange(2000,2100);year.setValue(date.year());year.setSuffix(' 年');year.setMinimumWidth(112);year.setAccessibleName(title+'年份');line.addWidget(year)
            month=QComboBox();month.addItems([f'{value:02d} 月' for value in range(1,13)]);month.setCurrentIndex(date.month()-1);month.setMinimumWidth(96);month.setAccessibleName(title+'月份');line.addWidget(month);line.addStretch()
            self.fields.append((year,month));layout.addLayout(line)
        self.error=label('','Muted');self.error.setStyleSheet('color:#B5473A;');layout.addWidget(self.error)
        actions=QHBoxLayout();actions.addStretch();actions.addWidget(button('取消',self.reject,'Quiet'));self.apply_button=button('应用月份',self.apply,'Primary');actions.addWidget(self.apply_button);layout.addLayout(actions)
        for year,month in self.fields:
            year.valueChanged.connect(self.validate);month.currentIndexChanged.connect(self.validate)
        self.validate()
    def dates(self):
        (start_year,start_month),(end_year,end_month)=self.fields
        start=QDate(start_year.value(),start_month.currentIndex()+1,1)
        end_first=QDate(end_year.value(),end_month.currentIndex()+1,1)
        return start,end_first.addMonths(1).addDays(-1)
    def validate(self):
        start,end=self.dates();valid=start<=end
        self.apply_button.setEnabled(valid)
        self.error.setText('结束月份不能早于开始月份。' if not valid else '')
    def apply(self):
        if self.apply_button.isEnabled():self.accept()

class ExportProgressDialog(QDialog):
    """导出进度。平台异步生成文件没有百分比，只展示真实步骤和等待时间。"""
    def __init__(self,title,on_stop,parent=None):
        super().__init__(parent)
        self.running=True;self.warning_text='';self.error_text='';self.elapsed_seconds=0
        self.setWindowTitle('导出进度');self.setMinimumWidth(540);self.resize(580,340)
        self.setAccessibleName('导出进度')
        layout=QVBoxLayout(self);layout.setContentsMargins(24,22,24,22);layout.setSpacing(14)
        self.heading=label('正在导出账单','Section');layout.addWidget(self.heading)
        subtitle=label(title,'Muted');subtitle.setWordWrap(True);layout.addWidget(subtitle)
        self.current_step=label('准备导出…');self.current_step.setWordWrap(True)
        self.current_step.setAccessibleName('当前导出步骤');layout.addWidget(self.current_step)
        self.progress_bar=QProgressBar();self.progress_bar.setRange(0,0)
        self.progress_bar.setTextVisible(False);layout.addWidget(self.progress_bar)
        self.elapsed=label('已等待 00:00','Muted');layout.addWidget(self.elapsed)
        layout.addWidget(label('最近步骤','Muted'))
        self.steps=QListWidget();self.steps.setAccessibleName('导出步骤记录')
        self.steps.setMinimumHeight(96);layout.addWidget(self.steps,1)
        actions=QHBoxLayout();actions.addStretch()
        self.action_button=button('停止导出',on_stop)
        self.action_button.setMinimumWidth(108);actions.addWidget(self.action_button)
        layout.addLayout(actions)
        self.clock=QTimer(self);self.clock.timeout.connect(self.tick);self.clock.start(1000)
    def tick(self):
        self.elapsed_seconds+=1
        minutes,seconds=divmod(self.elapsed_seconds,60)
        self.elapsed.setText(f'已等待 {minutes:02d}:{seconds:02d}')
    def add_step(self,message):
        message=str(message or '').strip()
        if not message:return
        self.current_step.setText(message)
        if not self.steps.count() or self.steps.item(self.steps.count()-1).text().split('  ',1)[-1]!=message:
            self.steps.addItem(dt.datetime.now().strftime('%H:%M:%S')+'  '+message)
            self.steps.scrollToBottom()
    def stopping(self):
        self.add_step('正在停止；已下载文件和平台任务会保留')
        self.action_button.setEnabled(False)
    def finish(self,stopped=False):
        self.running=False;self.clock.stop();self.progress_bar.hide()
        if stopped:title='已停止';detail='已下载文件和平台任务已保留，可从导出记录继续。'
        elif self.error_text:title='导出未完成';detail=self.error_text
        elif self.warning_text:title='导出完成，有提醒';detail=self.warning_text
        else:title='导出完成';detail='结果已保存到导出记录。'
        self.setWindowTitle(title);self.heading.setText(title);self.add_step(title+'：'+detail)
        self.action_button.setText('关闭');self.action_button.setEnabled(True)
        try:self.action_button.clicked.disconnect()
        except TypeError:pass
        self.action_button.clicked.connect(self.accept)
    def closeEvent(self,event):
        if self.running:event.ignore();return
        super().closeEvent(event)

def tmall_shop_name(username):
    """天猫子账号形如「店铺名:子账号」，店铺名取第一个冒号前的部分。"""
    text=username.strip()
    positions=[text.find(separator) for separator in (':','：') if separator in text]
    if not positions:return ''
    index=min(positions)
    return text[:index].strip() if text[index+1:].strip() else ''

class App(QMainWindow):
    def __init__(self,data=DATA):
        super().__init__();self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True)
        self.config_path=self.data/'settings.json';self.config=load_json(self.config_path,{},'设置')
        self.store=Store(self.data);self.events=queue.Queue();self.busy=False;self.pending=None;self.current=None;self.platform='jst';self.export_progress=None
        self.engine=Engine(self.data,self.store,self.notify,self.ask,self.config.get('chrome',''))
        self.enabled={p:self.config.get('platforms',{}).get(p,p=='jst') for p in PLATFORMS}
        self.setWindowTitle('电商账单');self.resize(1120,830);self.setMinimumSize(940,710)
        self.setStyleSheet(STYLE)
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central);layout.setContentsMargins(28,22,28,22);layout.setSpacing(12)
        heading=QHBoxLayout();titles=QVBoxLayout();titles.setSpacing(5);titles.addWidget(label('电商账单','Title'));titles.addWidget(label('按月导出，按店铺整理。','Muted'));heading.addLayout(titles);heading.addStretch();heading.addWidget(button('设置',self.settings,'Quiet'));layout.addLayout(heading)
        # 运行环境提示条：缺 Chrome / 运行端 / 扩展时才出现。
        self.environment_state={};self.environment_action_name='';self.environment_action_url=''
        self.chrome_install_dialog=None;self.chrome_missing_prompted=False
        self.extension_install_dialog=None;self.extension_missing_prompted=False
        self.notice=QFrame();self.notice.setObjectName('Notice');notice_row=QHBoxLayout(self.notice);notice_row.setContentsMargins(14,10,12,10);notice_row.setSpacing(12)
        notice_texts=QVBoxLayout();notice_texts.setSpacing(3);self.notice_title=label('','NoticeTitle');self.notice_detail=label('','Muted');self.notice_detail.setWordWrap(True)
        notice_texts.addWidget(self.notice_title);notice_texts.addWidget(self.notice_detail);notice_row.addLayout(notice_texts,1)
        self.notice_action=button('',self.environment_action);notice_row.addWidget(self.notice_action)
        self.notice.hide();layout.insertWidget(1,self.notice)
        period,row=panel();period_layout=QHBoxLayout();period_layout.setSpacing(10)
        last=dt.date.today().replace(day=1)-dt.timedelta(days=1)
        self.start=QDateEdit(QDate(last.year,last.month,1));self.end=QDateEdit(QDate(last.year,last.month,last.day))
        for field,name in ((self.start,'开始日期'),(self.end,'结束日期')):
            field.setDisplayFormat('yyyy-MM-dd');field.setCalendarPopup(True);field.setKeyboardTracking(False)
            field.setFixedWidth(190);field.setMinimumHeight(42);field.setLocale(QLocale(QLocale.Language.Chinese,QLocale.Country.China));field.setAccessibleName(name)
            calendar=field.calendarWidget();calendar.setGridVisible(True);calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday);calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader);calendar.setMinimumSize(310,280)
        period_layout.addWidget(label('导出日期','Section'));period_layout.addSpacing(10);period_layout.addWidget(label('开始','Muted'));period_layout.addWidget(self.start);period_layout.addWidget(label('结束','Muted'));period_layout.addWidget(self.end);period_layout.addStretch();row.addLayout(period_layout)
        quick=QHBoxLayout();quick.setSpacing(8);quick.addSpacing(94)
        self.month_range_button=button('按月份选择',self.choose_months);quick.addWidget(self.month_range_button)
        self.this_month_button=button('本月',self.this_month,'Quiet');quick.addWidget(self.this_month_button)
        self.month_button=button('上个月',self.last_month,'Quiet');quick.addWidget(self.month_button)
        quick.addStretch();self.period_hint=label('聚水潭按发货时间筛选','Muted');self.period_hint.setWordWrap(True);self.period_hint.setMaximumWidth(260);quick.addWidget(self.period_hint)
        self.start.dateChanged.connect(self.update_period_hint);self.end.dateChanged.connect(self.update_period_hint);row.addLayout(quick);layout.addWidget(period)
        body=QHBoxLayout();body.setSpacing(16);platform_frame,platform_layout=panel();platform_frame.setFixedWidth(226);platform_layout.addWidget(label('平台','Section'))
        self.platform_rows={};self.platform_buttons={};self.platform_checks={};self.platform_details={}
        for key,name in PLATFORMS.items():
            entry=QFrame();entry.setObjectName('PlatformRow');box=QVBoxLayout(entry);box.setContentsMargins(8,4,8,8);box.setSpacing(0);line=QHBoxLayout();line.setSpacing(8)
            check=QCheckBox();check.setAccessibleName('选择'+name+'平台');check.setChecked(self.enabled[key]);check.toggled.connect(lambda state,p=key:self.platform_toggle(p,state));line.addWidget(check)
            select=button(name,lambda checked=False,p=key:self.select_platform(p),'PlatformName');line.addWidget(select,1);box.addLayout(line)
            detail=label('','Muted');detail.setContentsMargins(8,0,0,0);box.addWidget(detail);platform_layout.addWidget(entry)
            self.platform_rows[key]=entry;self.platform_buttons[key]=select;self.platform_checks[key]=check;self.platform_details[key]=detail
        platform_layout.addStretch();platform_note=label('勾选平台，默认全选该平台店铺。','Muted');platform_note.setWordWrap(True);platform_layout.addWidget(platform_note);body.addWidget(platform_frame)
        selection_frame,selection_layout=panel();selection_heading=QHBoxLayout();self.selection_title=label('聚水潭登录账号','Section');selection_heading.addWidget(self.selection_title);selection_heading.addStretch();self.add_button=button('添加账号',lambda:self.edit_account(),'Quiet');selection_heading.addWidget(self.add_button);self.more_button=button('更多',lambda:None,'Quiet');menu=QMenu(self.more_button);menu.addAction('编辑登录信息',lambda:self.edit_account(self.current));menu.addAction('删除记录',self.delete_account);self.more_button.setMenu(menu);selection_heading.addWidget(self.more_button);selection_layout.addLayout(selection_heading)
        self.jst_note=label('默认导出全部店铺的订单商品数据，按店铺分 Sheet。','Muted');self.jst_note.setWordWrap(True);selection_layout.addWidget(self.jst_note)
        self.shop_options=QWidget();options=QHBoxLayout(self.shop_options);options.setContentsMargins(0,0,0,0);self.all_shops=QCheckBox('全选店铺');self.all_shops.toggled.connect(self.toggle_all);options.addWidget(self.all_shops);self.count=label('','Muted');options.addWidget(self.count);options.addStretch();self.search=QLineEdit();self.search.setPlaceholderText('搜索店铺名称');self.search.setClearButtonEnabled(True);self.search.setMaximumWidth(300);self.search.textChanged.connect(self.render_accounts);options.addWidget(self.search);selection_layout.addWidget(self.shop_options)
        self.accounts=QListWidget();self.accounts.setAccessibleName('聚水潭登录账号');self.accounts.currentItemChanged.connect(self.account_selected);self.accounts.itemChanged.connect(self.account_check)
        self.accounts_stack=QStackedWidget();self.accounts_stack.setMinimumHeight(112);self.accounts_stack.addWidget(self.accounts);self.account_empty=empty('添加聚水潭登录账号','可保存登录信息；需要验证时在 Chrome 完成登录。');self.accounts_stack.addWidget(self.account_empty);selection_layout.addWidget(self.accounts_stack,1)
        selection_footer=QHBoxLayout();self.hint=label('账号密码加密保存在本机。','Muted');self.hint.setWordWrap(True);selection_footer.addWidget(self.hint,1);self.sync_button=button('检查登录',self.sync);selection_footer.addWidget(self.sync_button);selection_layout.addLayout(selection_footer);body.addWidget(selection_frame,1);layout.addLayout(body,1)
        save=QHBoxLayout();save.setSpacing(10);save.addWidget(label('保存位置','Muted'));self.output=QLineEdit(self.config.get('output') or str(Path.home()/'Documents/电商账单'));self.output.setAccessibleName('导出保存位置');save.addWidget(self.output,1);save.addWidget(button('选择文件夹',self.choose_output));save.addWidget(button('打开',lambda:self.open_path(Path(self.output.text())),'Quiet'));layout.addLayout(save)
        history_frame,history_layout=panel();history_layout.setContentsMargins(16,12,16,8);history_heading=QHBoxLayout();history_heading.addWidget(label('导出记录','Section'));history_heading.addStretch();self.retry_button=button('继续未完成',self.retry,'Quiet');history_heading.addWidget(self.retry_button);self.open_file_button=button('打开文件',self.open_export,'Quiet');history_heading.addWidget(self.open_file_button);history_layout.addLayout(history_heading)
        self.history=QTableWidget(0,4);self.history.setHorizontalHeaderLabels(['日期范围','平台 / 店铺','状态','结果']);self.history.verticalHeader().hide();self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.history.setShowGrid(False);self.history.setWordWrap(False);self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.history.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch);self.history.cellDoubleClicked.connect(lambda *_:self.open_export());self.history.itemSelectionChanged.connect(self.history_actions)
        self.history_stack=QStackedWidget();self.history_stack.addWidget(self.history);self.history_stack.addWidget(empty('暂无导出记录','导出的文件和未完成任务会保存在这里。'));self.history_stack.setMinimumHeight(60);self.history_stack.setMaximumHeight(142);history_layout.addWidget(self.history_stack);layout.addWidget(history_frame)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.setTextVisible(False);self.progress.hide();layout.addWidget(self.progress)
        footer=QHBoxLayout();self.stage=label('准备就绪','Muted');self.stage.setWordWrap(True);footer.addWidget(self.stage,1);self.stop_button=button('停止',self.stop,'Quiet');self.stop_button.hide();footer.addWidget(self.stop_button);self.run_button=button('开始导出',self.export,'Primary');self.run_button.setMinimumWidth(154);footer.addWidget(self.run_button);layout.addLayout(footer)
        self.select_platform('jst');self.render_tasks();self.timer=QTimer(self);self.timer.timeout.connect(self.drain);self.timer.start(100)
        # 启动时若有状态文件读不出来（见 accounts.load_json），如实说一句：
        # 用户需要知道「记录为什么空了」以及「坏掉的那份被改名留在了哪里」，
        # 否则他会以为是自己点错了什么、或者以为记录被 APP 删了。
        self.startup_notice='；'.join(STARTUP_PROBLEMS)
        if self.startup_notice:self.stage.setText(self.startup_notice)
        # 启动就探一次运行环境。走后台线程 + 事件队列，因为 probe 有超时，
        # 最坏要等几秒，不能卡在界面构造里。
        self.check_environment()
    def notify(self,kind,value):self.events.put((kind,value))
    def ask(self,text,name,logout=False):
        event=threading.Event();result={};self.events.put(('ask',(text,name,logout,event,result)))
        # 每 0.2 秒醒一次去 checkpoint：这样「停止」能立刻把工作线程从等待里放出来，
        # 而不是一直卡到用户点对话框（点停止不会自动关掉那个对话框，stop() 里会关）。
        while not event.wait(.2):self.engine.checkpoint()
        if not result.get('ok'):
            # 先问一次停止标记：点「停止」时 stop() 会关掉对话框，于是这里也会走到
            # 「没确认」这条分支——若直接报「用户取消登录」，用户会以为自己点错了东西。
            # checkpoint 在已停止时会抛出「已停止…」，那才是他真正做的事。
            self.engine.checkpoint()
            raise RuntimeError('用户取消登录或退出确认')
    def drain(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='stage':
                    self.stage.setText(value)
                    if self.export_progress and self.export_progress.running:self.export_progress.add_step(value)
                elif kind=='tasks':self.render_tasks()
                elif kind=='shops':self.render_accounts(value)
                elif kind=='ask':self.login_prompt(*value)
                elif kind=='environment':self.show_environment(value)
                elif kind=='error':
                    if self.export_progress and self.export_progress.running:self.export_progress.error_text=value;self.export_progress.add_step('未完成：'+value)
                    else:QMessageBox.warning(self,'未完成',value)
                elif kind=='warning':
                    if self.export_progress and self.export_progress.running:self.export_progress.warning_text=value;self.export_progress.add_step('提醒：'+value)
                    else:QMessageBox.information(self,'导出提醒',value)
                elif kind=='idle':
                    self.busy=False;self.set_busy(False)
                    self.stage.show()
                    if self.pending:self.pending.reject();self.pending=None
                    if self.export_progress and self.export_progress.running:self.export_progress.finish(self.engine.cancelled)
        except queue.Empty:pass
    def set_busy(self,busy):
        for w in (*self.platform_buttons.values(),*self.platform_checks.values(),self.accounts,self.all_shops,self.start,self.end,self.month_range_button,self.this_month_button,self.month_button,self.search,self.add_button,self.more_button,self.output,self.run_button):w.setEnabled(not busy)
        self.progress.setVisible(busy);self.stop_button.setVisible(busy);self.sync_button.setEnabled(not busy and bool(self.selected()));self.history_actions()
    def check_environment(self):
        """后台探一次运行环境：probe 有超时、最坏几秒，不能卡在界面线程里。"""
        def run():
            try:self.notify('environment',environment.probe(chrome_override=self.config.get('chrome','')))
            except Exception as exc:self.notify('environment',{'daemon':False,'error':str(exc)})
        threading.Thread(target=run,daemon=True).start()
    def show_environment(self,state):
        self.environment_state=state or {};info=environment.describe(self.environment_state)
        self.notice_title.setText(info['title']);self.notice_detail.setText(info['detail'])
        self.environment_action_name=info['action'];self.environment_action_url=info['url']
        if info['action']:
            self.notice_action.setText('下载 Google Chrome' if info['verdict']=='chrome_missing' else '由 APP 自动安装' if info['action']=='install' else '在 Chrome 中安装扩展')
            self.notice.show()
        else:
            self.notice.hide()
        if info['verdict']=='chrome_missing' and not self.chrome_missing_prompted:
            self.chrome_missing_prompted=True
            self.prompt_chrome_install()
        elif info['verdict']!='chrome_missing' and self.chrome_install_dialog:
            self.chrome_install_dialog.close()
        if info['verdict']=='extension_missing' and not self.extension_missing_prompted:
            self.extension_missing_prompted=True
            self.prompt_extension_install()
        elif info['verdict']!='extension_missing' and self.extension_install_dialog:
            self.extension_install_dialog.close()
        # 设置对话框开着的话，把里面那一行一起刷新。对话框关掉后 Qt 会销毁控件，
        # 再访问就抛 RuntimeError，所以这里兜住并把引用清掉。
        if getattr(self,'settings_status',None) is not None:
            try:
                self.settings_status.setText(info['title']+'：'+info['detail'])
                self.settings_action.setVisible(bool(info['action']))
                if info['action']:self.settings_action.setText('下载 Google Chrome' if info['verdict']=='chrome_missing' else '由 APP 自动安装' if info['action']=='install' else '在 Chrome 中安装扩展')
            except RuntimeError:self.settings_status=None;self.settings_action=None
        # 就绪时顺手在底部说一句；但别盖掉启动时「状态文件读不出来」的那句提示。
        if info['verdict']=='ready' and not self.busy and not self.startup_notice:self.stage.setText(info['detail'])
    def prompt_chrome_install(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('需要安装 Google Chrome')
        dialog.setMinimumWidth(480)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(24,22,24,22);layout.setSpacing(14)
        layout.addWidget(label('未找到 Google Chrome','Section'))
        detail=label('电商账单使用你安装的 Chrome。点击「去安装」会在系统默认浏览器打开 Google 官方下载页；安装后回到设置中点击「重新检查」。','Muted')
        detail.setWordWrap(True);layout.addWidget(detail)
        actions=QHBoxLayout();actions.addStretch();actions.addWidget(button('稍后',dialog.reject,'Quiet'))
        def install():
            dialog.accept();self.environment_action()
        actions.addWidget(button('去安装',install,'Primary'));layout.addLayout(actions)
        dialog.finished.connect(lambda _code:setattr(self,'chrome_install_dialog',None) if self.chrome_install_dialog is dialog else None)
        self.chrome_install_dialog=dialog
        dialog.setModal(False);dialog.show();dialog.raise_();dialog.activateWindow()
    def prompt_extension_install(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('需要安装 Chrome 扩展')
        dialog.setMinimumWidth(480)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(24,22,24,22);layout.setSpacing(14)
        layout.addWidget(label('缺少 Kimi 浏览器扩展','Section'))
        detail=label('点击「在 Chrome 中安装」，APP 会打开本机 Google Chrome 并进入扩展安装页。安装完成后，回到设置中点击「重新检查」。','Muted')
        detail.setWordWrap(True);layout.addWidget(detail)
        actions=QHBoxLayout();actions.addStretch();actions.addWidget(button('稍后',dialog.reject,'Quiet'))
        def install():
            dialog.accept();self.environment_action()
        actions.addWidget(button('在 Chrome 中安装',install,'Primary'));layout.addLayout(actions)
        dialog.finished.connect(lambda _code:setattr(self,'extension_install_dialog',None) if self.extension_install_dialog is dialog else None)
        self.extension_install_dialog=dialog
        dialog.setModal(False);dialog.show();dialog.raise_();dialog.activateWindow()
    def environment_action(self):
        if self.busy:return
        if self.environment_action_name=='open':
            if environment.verdict(self.environment_state)=='extension_missing':
                def launch():
                    self.notify('stage','正在打开 Chrome 扩展安装页')
                    environment.open_extension_store(self.config.get('chrome',''))
                    self.notify('stage','已在 Chrome 中打开扩展安装页；安装后请重新检查')
                self.start_worker(launch)
            elif not QDesktopServices.openUrl(QUrl(self.environment_action_url)):
                QMessageBox.warning(self,'无法打开下载页面','请在浏览器中手动打开：'+self.environment_action_url)
        elif self.environment_action_name=='install':self.start_worker(self.install_environment)
    def install_environment(self):
        """由 APP 装运行端（用户点按钮才会走到这里）。跑在工作线程，进度写底部状态行。"""
        version,_=environment.install(progress=lambda text:self.notify('stage',text))
        self.notify('stage',f'运行端 {version} 已安装并启动')
        self.notify('environment',environment.probe(chrome_override=self.config.get('chrome','')))
    def login_prompt(self,text,name,logout,event,result):
        dialog=QDialog(self);dialog.setWindowTitle(('退出账号' if logout else '登录账号')+' · '+name);dialog.setMinimumWidth(460);v=QVBoxLayout(dialog);v.setContentsMargins(24,24,24,24);v.setSpacing(16);v.addWidget(label(name,'Section'));message=label(text);message.setWordWrap(True);v.addWidget(message);buttons=QHBoxLayout();buttons.addWidget(button('取消',dialog.reject));buttons.addStretch();buttons.addWidget(button('已退出，继续' if logout else '已登录，继续',dialog.accept,'Primary'));v.addLayout(buttons)
        def finished(code):result['ok']=code==QDialog.DialogCode.Accepted;event.set();self.pending=None
        dialog.finished.connect(finished);self.pending=dialog;dialog.setModal(False);dialog.show();dialog.raise_();dialog.activateWindow()
    def selected(self):return next((a for a in self.store.items if a['id']==self.current),None)
    def update_period_hint(self,*_):
        if self.platform!='tmall':self.period_hint.setText('聚水潭按发货时间筛选');self.period_hint.setToolTip('');return
        try:
            start=self.start.date().toString('yyyy-MM-dd');end=self.end.date().toString('yyyy-MM-dd');period=export_period('tmall',start,end);months=period['months']
            scope=months[0] if len(months)==1 else months[0]+' 至 '+months[-1]
            self.period_hint.setText('天猫按整月：'+scope);self.period_hint.setToolTip('天猫实际范围：'+period['start']+' 至 '+period['end']+'；跨月逐月导出。APP 日期不变。')
        except ValueError:self.period_hint.setText('开始日期不能晚于结束日期')
    def select_platform(self,platform):
        if self.busy:return
        if self.platform!=platform:self.current=None;self.search.blockSignals(True);self.search.clear();self.search.blockSignals(False)
        self.platform=platform;self.update_period_hint()
        for key,row in self.platform_rows.items():
            for widget in (row,self.platform_buttons[key]):widget.setProperty('selected',key==platform);widget.style().unpolish(widget);widget.style().polish(widget)
        self.render_accounts()
    def platform_toggle(self,platform,state):
        if self.busy:return
        self.enabled[platform]=state
        for a in self.store.items:
            if a['platform']==platform:a['enabled']=state
        self.store.save();self.save_settings();self.select_platform(platform)
    def platform_summary(self):
        for key,check in self.platform_checks.items():
            check.blockSignals(True);check.setChecked(self.enabled[key]);check.blockSignals(False)
            rows=[a for a in self.store.items if a['platform']==key];n=sum(bool(a.get('enabled',True)) for a in rows) if self.enabled[key] else 0
            self.platform_details[key].setText('全部店铺' if key=='jst' else f'已选 {n} / {len(rows)} 家店铺')
    def render_accounts(self,select_id=None):
        keep=select_id if isinstance(select_id,str) and any(a['id']==select_id for a in self.store.items) else self.current;self.accounts.blockSignals(True);self.accounts.clear();chosen=None
        jst=self.platform=='jst';name=PLATFORMS[self.platform];rows=[a for a in self.store.items if a['platform']==self.platform];search=self.search.text().strip().casefold() if not jst else ''
        self.selection_title.setText(name+'登录账号' if jst else name+'店铺');self.add_button.setText('添加账号' if jst else '添加店铺');self.accounts.setAccessibleName(name+'登录账号' if jst else name+'店铺');self.shop_options.setVisible(not jst);self.jst_note.setVisible(jst);self.sync_button.setVisible(True)
        for a in rows:
            if search not in a['name'].casefold():continue
            item=QListWidgetItem(a['name']);item.setData(USER,a['id']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(CHECKED if self.enabled[self.platform] and a.get('enabled',True) else UNCHECKED);self.accounts.addItem(item)
            if a['id']==keep:chosen=item
        self.accounts_stack.setCurrentIndex(0 if self.accounts.count() else 1)
        texts=self.account_empty.findChildren(QLabel);texts[0].setText('没有匹配店铺' if search and rows else '添加'+name+('登录账号' if jst else '店铺'));texts[1].setText('试试其他店铺名称。' if search and rows else '可保存登录信息；需要验证时在 Chrome 完成登录。' if jst else '填写与后台一致的店铺名称，每家店铺保存一组登录信息。')
        count=sum(bool(a.get('enabled',True)) for a in rows) if self.enabled[self.platform] else 0;self.count.setText(f'已选 {count} / {len(rows)}');self.all_shops.blockSignals(True);self.all_shops.setChecked(bool(rows) and count==len(rows));self.all_shops.blockSignals(False)
        self.hint.setText('账号密码加密保存在本机。' if jst else '聚核算月度明细与收入账单全量明细：按所选日期覆盖的整月导出，每月一个工作簿。');self.accounts.blockSignals(False);self.accounts.setCurrentItem(chosen or (self.accounts.item(0) if self.accounts.count() else None));self.account_selected(self.accounts.currentItem());self.platform_summary()
    def account_selected(self,item,*_):
        self.current=item.data(USER) if item else None;self.sync_button.setEnabled(bool(self.selected()) and not self.busy);self.more_button.setEnabled(bool(self.selected()) and not self.busy)
    def account_check(self,item):
        if self.busy:return
        a=next(a for a in self.store.items if a['id']==item.data(USER));a['enabled']=item.checkState()==CHECKED
        self.enabled[self.platform]=any(x.get('enabled',True) for x in self.store.items if x['platform']==self.platform);self.store.save();self.save_settings();self.render_accounts()
    def toggle_all(self,state):
        if self.busy or self.platform=='jst':return
        self.enabled[self.platform]=state
        for a in self.store.items:
            if a['platform']==self.platform:a['enabled']=state
        self.store.save();self.save_settings();self.render_accounts()
    def edit_account(self,account_id=None):
        if self.busy:return
        a=next((a for a in self.store.items if a['id']==account_id),None);dialog=QDialog(self);entity='账号' if self.platform=='jst' else '店铺';dialog.setWindowTitle(('编辑' if a else '添加')+entity);dialog.setMinimumWidth(430);layout=QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(18);layout.addWidget(label(PLATFORMS[self.platform]+entity,'Section'));form=QFormLayout();form.setSpacing(12);fields={}
        for key,title in [('name','账号名称' if self.platform=='jst' else '店铺名称'),('username','登录账号'),('password','登录密码')]:
            field=QLineEdit(a.get(key,'') if a and key!='password' else '');field.setAccessibleName(title)
            if key=='password':field.setEchoMode(QLineEdit.EchoMode.Password);field.setPlaceholderText('留空保留已保存密码' if a else '填写平台登录密码，留空则每次在 Chrome 里手动登录')
            if key=='name':field.setPlaceholderText('例如：公司主账号' if self.platform=='jst' else '例如：品牌旗舰店')
            fields[key]=field;form.addRow(title,field)
        if self.platform=='tmall' and not a:
            fields['name'].setPlaceholderText('由登录账号中冒号前的店铺名自动生成')
            fields['name'].setReadOnly(True)
            fields['username'].setPlaceholderText('例如：品牌旗舰店:子账号')
            fields['username'].textChanged.connect(lambda text:fields['name'].setText(tmall_shop_name(text)))
        layout.addLayout(form);hint=label('密码使用系统加密保存；登录时会自动填写账号密码、勾选登录协议并提交。平台要求滑块或短信验证时仍需在 Chrome 中完成验证。','Muted');hint.setWordWrap(True);layout.addWidget(hint);buttons=QHBoxLayout()
        if a:
            def forget():
                try:self.store.forget_password(a);QMessageBox.information(dialog,'已移除','此账号的已保存密码已移除')
                except Exception as e:QMessageBox.warning(dialog,'无法移除',str(e))
            buttons.addWidget(button('移除密码',forget,'Quiet'))
        buttons.addStretch();buttons.addWidget(button('取消',dialog.reject,'Quiet'))
        def save():
            try:
                if self.platform=='tmall' and not a and not fields['name'].text():
                    raise ValueError('天猫登录账号请填写为「店铺名:子账号」，店铺名称会自动取冒号前的文字')
                row=self.store.put(self.platform,fields['name'].text(),fields['username'].text(),fields['password'].text(),account_id)
            except Exception as e:QMessageBox.warning(dialog,'无法保存',str(e));return
            row['enabled']=True;self.enabled[self.platform]=True;self.store.save();self.save_settings();dialog.accept();self.render_accounts(row['id'])
        buttons.addWidget(button('保存'+entity,save,'Primary'));layout.addLayout(buttons);dialog.exec()
    def delete_account(self):
        if self.busy:return
        a=self.selected()
        if a and QMessageBox.question(self,'删除记录',f"删除「{a['name']}」的登录信息和保存密码？已导出文件保留。")==QMessageBox.StandardButton.Yes:
            try:self.store.forget_password(a);self.store.items.remove(a);self.store.save();self.current=None;self.render_accounts()
            except Exception as e:QMessageBox.warning(self,'无法删除',str(e))
    def start_worker(self,action,progress_title=None):
        if self.busy:return
        self.busy=True;self.engine.cancelled=False;self.set_busy(True)
        if progress_title:
            if self.export_progress:self.export_progress.close()
            dialog=ExportProgressDialog(progress_title,self.stop,self)
            self.export_progress=dialog
            dialog.finished.connect(lambda _code,current=dialog:setattr(self,'export_progress',None) if self.export_progress is current else None)
            self.progress.hide();self.stop_button.hide();self.stage.hide()
            dialog.show();dialog.raise_();dialog.activateWindow()
        def run():
            try:action()
            # 用 readable_error 统一转写：导出链路内部自己会把 OSError 变成中文，
            # 但工作线程里还有它包不到的地方（例如「检查登录」这条路），
            # 直接用 str(e) 会把「[Errno 28] No space left on device」原样弹给用户。
            except Exception as e:self.notify('error',readable_error(e))
            finally:self.notify('idle',None)
        threading.Thread(target=run,daemon=True).start()
    def sync(self):
        a=self.selected()
        if not a:QMessageBox.information(self,'添加记录','请先添加聚水潭账号或天猫店铺');return
        self.start_worker(lambda:self.engine.check_login(a))
    def export(self):
        if self.busy:return
        try:
            start,end=self.start.date().toString('yyyy-MM-dd'),self.end.date().toString('yyyy-MM-dd');validate_dates(start,end)
            if not self.output.text().strip():raise ValueError('请选择保存位置')
            accounts=[a for a in self.store.items if self.enabled[a['platform']] and a.get('enabled',True)]
            if not accounts:raise ValueError('请勾选平台，并添加聚水潭账号或平台店铺')
            self.save_settings();jobs=[(self.engine.create(a,start,end,self.output.text(),True,[]),a) for a in accounts];self.render_tasks()
            def run():
                for index,(task,a) in enumerate(jobs):
                    # 同一平台后面还有账号时必须先退出当前账号，否则下一个会沿用它的登录态；
                    # 后面没有同平台账号就不必退出，保留登录态（聚水潭的身份校验也能拦住串号，
                    # 但那要白跑一趟才报错）。
                    self.engine.checkpoint();self.engine.execute(task,a,next_same_platform(accounts,index))
                self.notify('stage',f'已完成 {len(jobs)} 个账号导出')
            self.start_worker(run,f'{start} 至 {end} · {len(jobs)} 个账号')
        except Exception as e:QMessageBox.warning(self,'无法导出',str(e))
    def render_tasks(self):
        selected=self.selected_task();identity=selected['id'] if selected else None;self.history.setRowCount(len(self.engine.tasks));states={'done':'已完成','failed':'未完成','running':'进行中','queued':'等待中','empty':'无订单'}
        for row,t in enumerate(self.engine.tasks):
            # 聚水潭按店铺分 Sheet；天猫报表无店铺列，按工作簿计数。
            unit='家店铺' if t['platform']=='jst' else '个工作簿'
            result=f"{t.get('sheetCount',0)} {unit} · {t.get('rows',0):,} 行明细" if t['status']=='done' else t.get('error') or t['stage']
            if t['status']=='done' and t.get('warning'):result+='；'+t['warning']
            state='已完成·有提醒' if t['status']=='done' and t.get('warning') else states[t['status']]
            values=[t['start']+' 至 '+t['end'],PLATFORMS[t['platform']]+' / '+t['accountName'],state,result]
            for col,text in enumerate(values):
                item=QTableWidgetItem(text);item.setData(USER,t['id']);item.setToolTip(text)
                if col==2:item.setForeground(QColor('#B36B1B' if t.get('warning') and t['status']=='done' else '#20744D' if t['status']=='done' else '#A34337' if t['status']=='failed' else '#52657F'))
                self.history.setItem(row,col,item)
            self.history.setRowHeight(row,38)
            if t['id']==identity:self.history.selectRow(row)
        self.history_stack.setCurrentIndex(0 if self.engine.tasks else 1);self.history_actions()
    def history_actions(self):
        t=self.selected_task();self.retry_button.setEnabled(bool(t and t['status'] in ('failed','queued') and not t.get('submissionUnknown')) and not self.busy);self.open_file_button.setEnabled(bool(t and (t.get('files') or (t.get('file') and Path(t['file']).is_file()))) and not self.busy)
    def selected_task(self):
        row=self.history.currentRow() if hasattr(self,'history') else -1
        if row<0:return None
        item=self.history.item(row,0);identity=item.data(USER) if item else None
        return next((t for t in self.engine.tasks if t['id']==identity),None)
    def retry(self):
        t=self.selected_task()
        if self.busy or not t or t['status'] not in ('failed','queued'):return
        if t.get('submissionUnknown'):QMessageBox.warning(self,'先核查平台任务','导出提交结果未知，请先到聚水潭异步导出管理核查，APP 不会重复提交。');return
        a=next((a for a in self.store.items if a['id']==t['accountId']),None)
        if not a:QMessageBox.warning(self,'账号不存在','请恢复原账号后继续');return
        self.start_worker(lambda:self.engine.execute(t,a),f"{PLATFORMS[t['platform']]} / {a['name']} · {t['start']} 至 {t['end']}")
    def stop(self):
        self.engine.stop();self.stage.setText('正在停止，已获取的任务和文件将保留')
        if self.export_progress and self.export_progress.running:self.export_progress.stopping()
        # 点「停止」时若正停在一个登录/退出确认对话框上，把它一起关掉：
        # 工作线程确实会被 checkpoint 放出来，但对话框不会自己消失，留着会让人以为
        # 「APP 还在等我确认登录」——而用户刚刚说的其实是不想继续了。
        dialog,self.pending=self.pending,None
        if dialog is not None:
            try:dialog.reject()
            except Exception:pass
    def open_export(self):
        t=self.selected_task()
        if not t:return
        # 天猫一个任务会产出多个工作簿（两个来源 × N 个月），此时打开它们所在的文件夹。
        files=t.get('files') or ([t['file']] if t.get('file') else [])
        if not files:return
        if len(files)==1:self.open_path(Path(files[0]));return
        try:folder=Path(os.path.commonpath([str(Path(f).parent) for f in files]))
        except ValueError:folder=Path(files[0]).parent
        self.open_path(folder)
    def open_path(self,path):
        try:
            if path.suffix.lower()!='.xlsx':path.mkdir(parents=True,exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):raise RuntimeError('系统无法打开此文件或文件夹')
        except Exception as e:QMessageBox.warning(self,'无法打开',str(e))
    def last_month(self):
        if self.busy:return
        last=dt.date.today().replace(day=1)-dt.timedelta(days=1);self.start.setDate(QDate(last.year,last.month,1));self.end.setDate(QDate(last.year,last.month,last.day))
    def this_month(self):
        if self.busy:return
        today=QDate.currentDate();self.start.setDate(QDate(today.year(),today.month(),1));self.end.setDate(QDate(today.year(),today.month(),1).addMonths(1).addDays(-1))
    def choose_months(self):
        if self.busy:return
        dialog=MonthRangeDialog(self.start.date(),self.end.date(),self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            start,end=dialog.dates();self.start.setDate(start);self.end.setDate(end)
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,'选择保存文件夹',self.output.text())
        if path:self.output.setText(path);self.save_settings()
    def save_settings(self):
        self.config.update(output=self.output.text() if hasattr(self,'output') else self.config.get('output',''),platforms=self.enabled);atomic(self.config_path,self.config)
    def settings(self):
        if self.busy:return
        dialog=QDialog(self);dialog.setWindowTitle('设置');dialog.setMinimumWidth(500);layout=QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
        layout.addWidget(label(f'电商账单 · 版本 {VERSION}','Muted'))
        # 运行环境放最上面：缺组件时这是最需要看到的一条，也是这里唯一能采取动作的地方。
        layout.addWidget(label('浏览器运行环境','Section'));environment_row=QHBoxLayout();environment_row.setSpacing(10)
        self.settings_status=label('','Muted');self.settings_status.setWordWrap(True);environment_row.addWidget(self.settings_status,1)
        self.settings_action=button('',self.environment_action,'Quiet');environment_row.addWidget(self.settings_action)
        def recheck():self.settings_status.setText('正在检查…');self.check_environment()
        environment_row.addWidget(button('重新检查',recheck,'Quiet'));layout.addLayout(environment_row)
        self.show_environment(self.environment_state)
        hint=label('账号密码由当前系统用户加密保存。\n浏览器操作使用正在运行的 Google Chrome。\n需要的运行端和扩展状态见上方。','Muted');hint.setWordWrap(True);layout.addWidget(hint)
        def save():
            self.save_settings();dialog.accept()
        row=QHBoxLayout();row.addStretch();row.addWidget(button('取消',dialog.reject,'Quiet'));row.addWidget(button('保存',save,'Primary'));layout.addLayout(row);dialog.exec();self.settings_status=None;self.settings_action=None
    def closeEvent(self,event):
        if self.busy:QMessageBox.information(self,'正在运行','请先点击「停止」，等待当前步骤完成再退出。');event.ignore();return
        self.save_settings();event.accept()

def self_test(path):
    result={'version':VERSION,'pythonBundled':bool(getattr(sys,'frozen',False)),'pyqt6':True,'errors':[]}
    if os.name=='nt':
        try:result['dpapi']=crypt(crypt(b'commerce-test'),True)==b'commerce-test'
        except Exception as e:result['errors'].append(str(e))
    try:
        window=QWidget();window.resize(400,300);window.show();QApplication.processEvents();window.close();result['gui']=True
    except Exception as e:result['errors'].append(str(e))
    # The package can be built on a clean Windows PC before the extension is
    # installed. Runtime readiness is reported separately and enforced when
    # a browser task starts.
    try:
        state=environment.probe(timeout=4)
        result['webbridge']={'chromeInstalled':bool(state.get('chrome_installed')),
                             'daemon':bool(state.get('daemon')),
                             'extensionConnected':bool(state.get('extension_connected')),
                             'version':state.get('version'),'extensionVersion':state.get('extension_version'),
                             'verdict':environment.describe(state)['verdict']}
    except Exception as e:result['webbridge']={'verdict':'error','error':str(e)}
    # 只验文件在不在不够：写出链路要真的跑一遍，并且把成果读回来核对。
    # 现在是内置的流式写入器（不再依赖 node 与 @oai/artifact-tool），所以要验的是
    # 「写出来的工作簿能被自己的读取器读回、且长数字仍是文本」。
    try:
        import tempfile, xlsx_writer
        from tables import read_xlsx
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);rows=folder/'rows.jsonl'
            rows.write_text(json.dumps(['验收店铺','1234567890123456789',39.9],ensure_ascii=False)+'\n',encoding='utf8')
            index={'headers':['店铺名称','订单号','金额'],'dateColumns':[],'numericColumns':[2],
                   'rows':1,'orders':1,'shops':[{'id':'1','name':'验收店铺','rows':1,
                   'path':str(rows),'sheetName':'验收店铺'}]}
            (folder/'sheets.json').write_text(json.dumps(index,ensure_ascii=False),encoding='utf8')
            output=folder/'out.xlsx'
            xlsx_writer.write(output,folder/'sheets.json')
            headers,written=read_xlsx(output)
            ok=bool(written) and headers==index['headers'] and written[0][1]=='1234567890123456789'
            result['xlsxPipeline']=ok
            result['xlsxRoundTrip']=written[0][1] if written else None
            if not ok:result['errors'].append(f'xlsx pipeline: 写出的工作簿读回不一致（{headers} / {written[:1]}）')
    except Exception as e:result['errors'].append('xlsx pipeline: '+str(e))
    result['PASS']=bool(result.get('gui') and result.get('xlsxPipeline') and not result['errors'] and (os.name!='nt' or result.get('dpapi')))
    Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return 0 if result['PASS'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--self-test');parser.add_argument('--data-dir');args=parser.parse_args();qt=QApplication(sys.argv[:1]);qt.setApplicationName('电商账单');qt.setFont(QFont('PingFang SC' if sys.platform=='darwin' else 'Microsoft YaHei UI',10));qt.setStyle('Fusion');QLocale.setDefault(QLocale(QLocale.Language.Chinese,QLocale.Country.China))
    if args.self_test:sys.exit(self_test(args.self_test))
    try:
        data=Path(args.data_dir) if args.data_dir else DATA;data.mkdir(parents=True,exist_ok=True);instance_lock=QLockFile(str(data/'app.lock'))
        if not instance_lock.tryLock(100):QMessageBox.information(None,'电商账单已打开','请使用已经打开的电商账单窗口。');sys.exit(0)
        window=App(data);window.show();sys.exit(qt.exec())
    except Exception as e:QMessageBox.critical(None,'APP 无法启动',str(e));sys.exit(1)
