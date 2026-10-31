import XCTest
final class BridgeUITests: XCTestCase {
    func testPairAndAnswerOverRealTLSBridge() {
        XCUIDevice.shared.orientation = .portrait
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing"]
        app.launch()
        let address = app.textFields["bridgeURL"]
        XCTAssertTrue(address.waitForExistence(timeout: 5))
        address.tap(); address.typeText(TestBridge.url)
        app.secureTextFields["pairingKey"].tap(); app.secureTextFields["pairingKey"].typeText(TestBridge.credential)
        app.textFields["fingerprint"].tap(); app.textFields["fingerprint"].typeText(TestBridge.pin)
        app.swipeUp()
        app.buttons["Connect securely"].tap()
        XCTAssertTrue(app.tabBars.buttons["More"].waitForExistence(timeout: 15))
        app.tabBars.buttons["More"].tap()
        app.buttons["Overview"].tap()
        XCTAssertTrue(app.staticTexts["Bridge connected"].waitForExistence(timeout: 15))
        let overview = XCTAttachment(screenshot: app.screenshot()); overview.name = "Connected overview"; overview.lifetime = .keepAlways; add(overview)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["moreInbox"].tap()
        app.staticTexts["Confirm the phone reply path?"].tap()
        app.buttons["Confirmed"].tap()
        app.buttons["Send answer"].tap()
        XCTAssertTrue(app.buttons["allowAISharing"].waitForExistence(timeout: 5))
        app.buttons["allowAISharing"].tap()
        XCTAssertTrue(app.staticTexts["Saved · awaiting agent"].waitForExistence(timeout: 10))
        let answered = XCTAttachment(screenshot: app.screenshot()); answered.name = "Reply saved"; answered.lifetime = .keepAlways; add(answered)
        app.buttons["Done"].tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["Projects"].tap()
        app.staticTexts["Integration project"].tap()
        let direction = app.textFields["projectDirection"]
        let editor = app.textViews["projectDirection"]
        if direction.waitForExistence(timeout: 2) { direction.tap(); direction.typeText("Prioritize the import bug") }
        else { XCTAssertTrue(editor.waitForExistence(timeout: 2)); editor.tap(); editor.typeText("Prioritize the import bug") }
        app.buttons["Send direction"].tap()
        XCTAssertTrue(app.staticTexts["Queued for this agent’s next checkpoint."].waitForExistence(timeout: 8))
        app.buttons["Pause at checkpoint"].tap()
        XCTAssertTrue(app.staticTexts["Pause at your next safe checkpoint."].waitForExistence(timeout: 8))
        app.buttons["Resume"].tap()
        XCTAssertTrue(app.staticTexts["Resume work using the latest project directions."].waitForExistence(timeout: 8))
        app.tabBars.buttons["Terminal"].tap()
        app.buttons["Open terminal"].tap()
        XCTAssertTrue(app.staticTexts["Connected · running"].waitForExistence(timeout: 15))
        app.buttons["Interrupt with Control C"].tap()
        XCTAssertTrue(app.staticTexts["Input accepted by bridge"].waitForExistence(timeout: 10))
        XCUIDevice.shared.orientation = .landscapeLeft
        let landscapeLayout = expectation(for: NSPredicate { _, _ in app.frame.width > app.frame.height }, evaluatedWith: nil)
        wait(for: [landscapeLayout], timeout: 5)
        // A real interaction waits for the rotated layout to settle before capture.
        app.buttons["Interrupt with Control C"].tap()
        XCTAssertTrue(app.staticTexts["Input accepted by bridge"].waitForExistence(timeout: 5))
        let landscape = XCTAttachment(screenshot: app.screenshot()); landscape.name = "Native fixture terminal landscape"; landscape.lifetime = .keepAlways; add(landscape)
        XCUIDevice.shared.orientation = .portrait
        let controls = XCTAttachment(screenshot: app.screenshot()); controls.name = "Project direction and flow controls"; controls.lifetime = .keepAlways; add(controls)
    }
}
