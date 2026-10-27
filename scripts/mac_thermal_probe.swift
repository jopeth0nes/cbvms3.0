// Numeric-only macOS pressure indicators. No serials, processes or user data.
import Foundation

let info = ProcessInfo.processInfo
let names = ["nominal", "fair", "serious", "critical"]
let state = Int(info.thermalState.rawValue)
let row: [String: Any] = ["thermal_state": state,
    "thermal_name": state < names.count ? names[state] : "unknown",
    "low_power_mode": info.isLowPowerModeEnabled,
    "active_processors": info.activeProcessorCount]
let data = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
