-- CloudStream TV Android TV emulator launcher
-- Emulator zaten calisiyorsa one getirir, calismiyorsa run-emulator.sh'i arka planda baslatir.

-- Launcher script sits next to the .app bundle (repo-relative, no hardcoded home path).
on launcherPath()
	set appPath to POSIX path of (path to me)
	if appPath ends with "/" then set appPath to text 1 thru -2 of appPath
	return (do shell script "dirname " & quoted form of appPath) & "/run-emulator.sh"
end launcherPath

on emuRunning()
	-- [c] hilesi: pgrep -f kendi sh -c komut satirini eslestirmesin
	try
		set r to do shell script "/usr/bin/pgrep -f 'qemu-system-aarch64.*[c]loudstream_tv' > /dev/null 2>&1 && echo yes || echo no"
		if r is "yes" then return true
	end try
	return false
end emuRunning

on bringToFront()
	try
		tell application "System Events"
			set procs to (every process whose name contains "qemu-system-aarch64")
			if (count of procs) > 0 then set frontmost of (item 1 of procs) to true
		end tell
	end try
	-- erisilebilirlik izni yoksa sessizce gecilir
end bringToFront

on showError(msg)
	try
		display dialog msg buttons {"Tamam"} default button 1 with icon stop with title "CloudStream TV"
	end try
end showError

on run
	try
		if emuRunning() then
			bringToFront()
			return
		end if
		
		set ok to "no"
		try
			set ok to do shell script "test -x " & quoted form of launcherPath() & " && echo yes || echo no"
		end try
		if ok is not "yes" then
			showError("Baslatici bulunamadi veya calistirilabilir degil:" & return & launcherPath())
			return
		end if
		
		do shell script "mkdir -p \"$HOME/Library/Logs\"; nohup " & quoted form of launcherPath() & " > \"$HOME/Library/Logs/CloudStreamTV-Launcher.log\" 2>&1 &"
		
		set started to false
		repeat 30 times
			delay 2
			if emuRunning() then
				set started to true
				exit repeat
			end if
		end repeat
		
		if not started then
			showError("Emulator baslatilamadi." & return & return & "Ayrinti icin log: ~/Library/Logs/CloudStreamTV-Launcher.log")
			return
		end if
		
		bringToFront()
	on error errMsg number errNum
		if errNum is -128 then return
		showError("CloudStream TV baslatilamadi." & return & return & "Hata: " & errMsg)
	end try
end run

