# Capynet APT Repository

This branch contains the APT repository for Tube Sync.

## Installation

```bash
# Add GPG key
curl -fsSL https://capynet.github.io/tubesync/capynet-apt.gpg | sudo gpg --dearmor -o /usr/share/keyrings/capynet.gpg

# Add repository
echo "deb [signed-by=/usr/share/keyrings/capynet.gpg] https://capynet.github.io/tubesync stable main" | sudo tee /etc/apt/sources.list.d/capynet.list

# Install
sudo apt update
sudo apt install tubesync
```

## Updates

Run `apt upgrade` to get the latest version.
