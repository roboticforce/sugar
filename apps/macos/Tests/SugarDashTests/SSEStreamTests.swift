import XCTest
@testable import SugarDash

/// Regression tests for the streaming line splitter and the defensive parser
/// flush (Foundation's AsyncLineSequence drops the blank SSE separator).
final class SSEStreamTests: XCTestCase {
    private func lines(_ text: String) -> [String] {
        var splitter = SSELineSplitter()
        var out: [String] = []
        for byte in Array(text.utf8) {
            if let line = splitter.consume(byte: byte) {
                out.append(line)
            }
        }
        return out
    }

    func testSplitterPreservesBlankSeparator() {
        XCTAssertEqual(
            lines("event: hello\ndata: {}\n\n"),
            ["event: hello", "data: {}", ""]
        )
    }

    func testSplitterHandlesCRLF() {
        XCTAssertEqual(
            lines("event: x\r\ndata: 1\r\n\r\n"),
            ["event: x", "data: 1", ""]
        )
    }

    func testSplitterLeavesPartialLineInBuffer() {
        var splitter = SSELineSplitter()
        var emitted: [String] = []
        for byte in Array("data: par".utf8) {
            if let line = splitter.consume(byte: byte) { emitted.append(line) }
        }
        XCTAssertEqual(emitted, [])
        XCTAssertEqual(splitter.consume(byte: UInt8(ascii: "\n")), "data: par")
    }

    func testParserEmitsWhenBlankLineArrives() {
        var parser = SSEParser()
        XCTAssertNil(parser.consume(line: "event: hello"))
        XCTAssertNil(parser.consume(line: "data: {\"a\": 1}"))
        let event = parser.consume(line: "")
        XCTAssertEqual(event?.event, "hello")
        XCTAssertEqual(String(decoding: event!.data, as: UTF8.self), "{\"a\": 1}")
    }

    func testParserFlushesWhenSeparatorIsMissing() {
        var parser = SSEParser()
        XCTAssertNil(parser.consume(line: "event: hello"))
        XCTAssertNil(parser.consume(line: "data: 1"))
        // No blank line: the next `event:` must flush the pending one.
        let pending = parser.consume(line: "event: snapshot")
        XCTAssertEqual(pending?.event, "hello")
        XCTAssertEqual(String(decoding: pending!.data, as: UTF8.self), "1")
        XCTAssertNil(parser.consume(line: "data: 2"))
        XCTAssertEqual(parser.consume(line: "")?.event, "snapshot")
    }
}
