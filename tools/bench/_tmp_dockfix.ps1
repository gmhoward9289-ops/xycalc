$p="$env:USERPROFILE\.docker\config.json"
$j=@{auths=@{};currentContext='desktop-linux'}
$utf8=New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText($p, ($j|ConvertTo-Json -Compress), $utf8)
'docker config fixed' | Out-File C:\Users\Owner\lab\docker-cfg2.out