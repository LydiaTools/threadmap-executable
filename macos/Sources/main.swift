import AppKit
import WebKit
import Foundation

let localURL = URL(string: "http://127.0.0.1:8771/")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var service: Process?
    private var startedService = false
    private var sourceRoot: URL!
    private var grantedFolder: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        sourceRoot = Bundle.main.url(forResource: "source-path", withExtension: "txt")
            .flatMap { try? String(contentsOf: $0, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) }
            .map(URL.init(fileURLWithPath:))
        NSApp.applicationIconImage = makeIcon()
        restoreFolderAccess()
        makeMenu()
        makeWindow()
        prepareServiceAndLoad()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard startedService, let service, service.isRunning else { return .terminateNow }
        if hasBusyWork() {
            showAlert(title: "还有任务没结束或没确认", message: "本应用启动的本机服务将继续保留，避免中断执行或规划任务。稍后可重新打开“我的任务地图”。")
        } else {
            service.terminate()
        }
        return .terminateNow
    }

    private func makeWindow() {
        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 760), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "我的任务地图"
        window.center()
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func makeMenu() {
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于我的任务地图", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出我的任务地图", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let appItem = NSMenuItem(); appItem.submenu = appMenu; menu.addItem(appItem)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = editMenu; menu.addItem(editItem)
        let viewItem = NSMenuItem(title: "窗口", action: nil, keyEquivalent: "")
        let viewMenu = NSMenu(title: "窗口")
        viewMenu.addItem(withTitle: "选择资料文件夹…", action: #selector(chooseFolder), keyEquivalent: "")
        viewMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        viewMenu.addItem(withTitle: "重新载入本机页面", action: #selector(reloadLocal), keyEquivalent: "r")
        viewItem.submenu = viewMenu; menu.addItem(viewItem)
        NSApp.mainMenu = menu
    }

    private func restoreFolderAccess() {
        guard let data = UserDefaults.standard.data(forKey: "referenceFolder") else { return }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: data, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &stale) else { return }
        if url.startAccessingSecurityScopedResource() { grantedFolder = url }
    }

    @objc private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.title = "允许读取资料文件夹"
        panel.message = "部分旧 Codex 记录在外接盘。请选择存放这些记录的文件夹，只用于读取已有记录，不移动或删除文件。"
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: "/Volumes/Obsidian知识库")
        panel.prompt = "允许读取此文件夹"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            do {
                let bookmark = try url.bookmarkData(options: [.withSecurityScope, .securityScopeAllowOnlyReadAccess], includingResourceValuesForKeys: nil, relativeTo: nil)
                UserDefaults.standard.set(bookmark, forKey: "referenceFolder")
                self.grantedFolder?.stopAccessingSecurityScopedResource()
                self.grantedFolder = url
                _ = url.startAccessingSecurityScopedResource()
                self.showAlert(title: "已保存你的选择", message: "请退出后重新打开应用，再检查任务能否正常开始。选择文件夹不等于执行已经通过检查。")
            } catch { self.showAlert(title: "没有保存成功", message: error.localizedDescription) }
        }
    }

    @objc private func showAbout() { showAlert(title: "我的任务地图", message: "本机个人版。页面与执行记录留在本机；不安装后台常驻服务。") }
    @objc private func reloadLocal() { webView.load(URLRequest(url: localURL, cachePolicy: .reloadIgnoringLocalCacheData)) }

    private func prepareServiceAndLoad() {
        DispatchQueue.global(qos: .userInitiated).async {
            if self.healthCheck() { self.loadOnMain(); return }
            guard let root = self.sourceRoot, FileManager.default.fileExists(atPath: root.appendingPathComponent("threadmap_server.py").path) else {
                self.failOnMain("找不到任务地图源码", "应用依赖原始目录，未启动任何服务。请保留 Developer/threadmap-executable-design 后重新构建应用。")
                return
            }
            self.startService(at: root)
        }
    }

    private func startService(at root: URL) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        task.arguments = ["threadmap_server.py", "--port", "8771"]
        task.currentDirectoryURL = root
        let errors = Pipe(); task.standardError = errors; task.standardOutput = Pipe()
        do { try task.run() } catch { failOnMain("无法启动本机服务", "\(error.localizedDescription)"); return }
        service = task; startedService = true
        for _ in 0..<24 {
            if healthCheck() { loadOnMain(); return }
            if !task.isRunning {
                let text = String(data: errors.fileHandleForReading.availableData, encoding: .utf8) ?? ""
                let msg = text.contains("Address already in use") ? "8771 端口已被其他程序占用，且它不是有效的任务地图服务。本应用没有终止该程序。" : "本机服务启动后退出。\n\(text)"
                failOnMain("无法使用本机服务", msg); return
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
        failOnMain("本机服务未就绪", "暂时没有收到有效回应。服务没有被强制终止；可稍后重新打开应用。")
    }

    private func readState() -> [String: Any]? {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8771/api/state")!)
        request.timeoutInterval = 0.7
        let sem = DispatchSemaphore(value: 0)
        let lock = NSLock()
        var state: [String: Any]?
        let task = URLSession.shared.dataTask(with: request) { data, response, _ in
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let data, let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               root["version"] as? Int == 2, root["nodes"] is [[String: Any]], root["limits"] is [String: Any] {
                lock.lock(); state = root; lock.unlock()
            }
            sem.signal()
        }
        task.resume()
        if sem.wait(timeout: .now() + 1) == .timedOut { task.cancel(); return nil }
        lock.lock(); defer { lock.unlock() }; return state
    }

    private func healthCheck() -> Bool { readState() != nil }

    private func hasBusyWork() -> Bool {
        guard let root = readState(), let nodes = root["nodes"] as? [[String: Any]] else { return true }
        return nodes.contains { node in
            let status = node["status"] as? String ?? ""
            let planning = node["planningStatus"] as? String ?? ""
            return ["sent", "running", "receipt_unknown"].contains(status) || ["sent", "running", "receipt_unknown"].contains(planning)
        }
    }

    private func loadOnMain() { DispatchQueue.main.async { self.webView.load(URLRequest(url: localURL, cachePolicy: .reloadIgnoringLocalCacheData)) } }
    private func failOnMain(_ title: String, _ message: String) { DispatchQueue.main.async { self.showAlert(title: title, message: message) } }
    private func showAlert(title: String, message: String) { let alert = NSAlert(); alert.messageText = title; alert.informativeText = message; alert.addButton(withTitle: "好"); alert.runModal() }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "http", url.host == "127.0.0.1", url.port == 8771 { decisionHandler(.allow); return }
        if url.scheme == "http" || url.scheme == "https" || url.scheme == "codex" { NSWorkspace.shared.open(url); decisionHandler(.cancel); return }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = "我的任务地图"; alert.informativeText = message; alert.addButton(withTitle: "好")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = "我的任务地图"; alert.informativeText = message; alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { response in completionHandler(response == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert(); alert.messageText = "我的任务地图"; alert.informativeText = prompt; alert.addButton(withTitle: "添加"); alert.addButton(withTitle: "取消")
        let input = NSTextField(string: defaultText ?? ""); input.frame = NSRect(x: 0, y: 0, width: 320, height: 24); alert.accessoryView = input; alert.window.initialFirstResponder = input
        alert.beginSheetModal(for: window) { response in completionHandler(response == .alertFirstButtonReturn ? input.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, url.scheme == "http" || url.scheme == "https" || url.scheme == "codex" { NSWorkspace.shared.open(url) }
        return nil
    }

    private func makeIcon() -> NSImage {
        let image = NSImage(size: NSSize(width: 512, height: 512)); image.lockFocus()
        NSColor(calibratedRed: 0.32, green: 0.42, blue: 0.96, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 24, y: 24, width: 464, height: 464), xRadius: 96, yRadius: 96).fill()
        NSColor.white.setStroke(); let p = NSBezierPath(); p.lineWidth = 28
        p.move(to: NSPoint(x: 145, y: 350)); p.line(to: NSPoint(x: 255, y: 256)); p.line(to: NSPoint(x: 370, y: 350)); p.move(to: NSPoint(x: 255, y: 256)); p.line(to: NSPoint(x: 255, y: 140)); p.stroke()
        for point in [NSPoint(x:145,y:350),NSPoint(x:255,y:256),NSPoint(x:370,y:350),NSPoint(x:255,y:140)] { NSBezierPath(ovalIn: NSRect(x:point.x-31,y:point.y-31,width:62,height:62)).fill() }
        image.unlockFocus(); return image
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
