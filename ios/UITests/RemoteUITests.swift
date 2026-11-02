import XCTest
final class RemoteUITests: XCTestCase {
    func testOnboardingAndSampleNativeTerminal() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing"]
        app.launch()
        app.swipeUp()
        app.buttons["How to set up your computer"].tap()
        XCTAssertTrue(app.staticTexts["1 · Prepare your computer"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        for _ in 0..<4 where !app.buttons["Explore demo"].isHittable { app.swipeUp() }
        app.buttons["Explore demo"].tap()
        app.tabBars.buttons["Devices"].tap()
        XCTAssertTrue(app.staticTexts["My computer"].waitForExistence(timeout: 5))
        app.tabBars.buttons["Terminal"].tap()
        app.buttons["Open terminal"].tap()
        XCTAssertTrue(app.staticTexts["Sample · no network"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Interrupt with Control C"].isEnabled)
        let terminal = XCTAttachment(screenshot: app.screenshot()); terminal.name = "Native sample terminal"; terminal.lifetime = .keepAlways; add(terminal)
    }
    func testDemoInboxAndProjectNavigation() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing"]
        app.launch()
        for _ in 0..<4 where !app.buttons["Explore demo"].isHittable { app.swipeUp() }
        app.buttons["Explore demo"].tap()
        XCTAssertTrue(app.staticTexts["Demo · sample tasks only"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["sendTask"].isEnabled)
        app.tabBars.buttons["More"].tap()
        app.buttons["Overview"].tap()
        XCTAssertTrue(app.staticTexts["Demo · sample projects"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["moreInbox"].tap()
        XCTAssertTrue(app.staticTexts["Keep the beta private or invite the team?"].exists)
        app.staticTexts["Keep the beta private or invite the team?"].tap()
        XCTAssertTrue(app.buttons["Keep it private"].exists)
    }
    func testConsoleStartsWithWorkAndOffersDesktopDownload() {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing"]
        app.launch()
        let download = app.descendants(matching: .any).matching(identifier: "downloadDesktop").firstMatch
        XCTAssertTrue(download.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Your projects.\nWithin reach."].exists)
        for _ in 0..<4 where !app.buttons["Explore demo"].isHittable { app.swipeUp() }
        app.buttons["Explore demo"].tap()
        XCTAssertTrue(app.staticTexts["Demo · sample tasks only"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Work, sent home."].exists)
        XCTAssertTrue(app.staticTexts["DISPATCH"].exists)
        XCTAssertFalse(app.buttons["sendTask"].isEnabled)
        let console = XCTAttachment(screenshot: app.screenshot())
        console.name = "Task console · sample data"
        console.lifetime = .keepAlways
        add(console)
        app.tabBars.buttons["More"].tap()
        app.buttons["Settings"].tap()
        let settingsDownload = app.descendants(matching: .any).matching(identifier: "downloadDesktopSettings").firstMatch
        for _ in 0..<4 where !settingsDownload.isHittable { app.swipeUp() }
        XCTAssertTrue(settingsDownload.exists)
    }

}
