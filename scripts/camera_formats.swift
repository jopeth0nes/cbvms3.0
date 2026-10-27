// Read-only AVFoundation format inventory. Never emit device unique IDs.
import AVFoundation
import CoreMedia
import Foundation

let devices = AVCaptureDevice.DiscoverySession(
    deviceTypes: [.builtInWideAngleCamera, .external], mediaType: .video,
    position: .unspecified).devices
let rows: [[String: Any]] = devices.map { device in
    ["name": device.localizedName, "formats": device.formats.map { format in
        let size = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
        return ["width": Int(size.width), "height": Int(size.height),
                "fps_ranges": format.videoSupportedFrameRateRanges.map {
                    ["min": $0.minFrameRate, "max": $0.maxFrameRate]
                }] as [String: Any]
    }]
}
let data = try JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted, .sortedKeys])
print(String(decoding: data, as: UTF8.self))
