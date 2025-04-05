#!/bin/bash

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Step 1: Check if paru is installed, if not, install paru
if ! command_exists paru; then
    echo "Paru not found, installing paru..."

    # Clone the paru AUR repository
    git clone https://aur.archlinux.org/paru.git

    # Change to the paru directory
    cd paru

    # Build and install paru
    makepkg -si --noconfirm

    # Go back to the home directory and remove the cloned repository
    cd ..
    rm -rf paru

    echo "Paru installed successfully."
else
    echo "Paru is already installed."
fi

# Step 2: Check if 'fakeroot' is installed, if not, install it
if ! command_exists fakeroot; then
    echo "fakeroot not found, installing it..."
    sudo pacman -S --noconfirm fakeroot
else
    echo "fakeroot is already installed."
fi

# Step 3: Set signature level to 'Never' if not already set
echo "Checking if signature level is set to 'Never'..."

if ! grep -q "SigLevel = Never" /etc/pacman.conf; then
    echo "Setting SigLevel to 'Never' in pacman.conf..."
    sudo sed -i 's/^SigLevel = .*/SigLevel = Never/g' /etc/pacman.conf
else
    echo "SigLevel is already set to 'Never'."
fi

# Step 4: Initialize and Configure Chaotic-AUR Keyring

# Initialize pacman keyring (if not already initialized)
echo "Initializing pacman keyring..."
sudo pacman-key --init

# Populate the Arch keyring
sudo pacman-key --populate archlinux

# Import the primary key for Chaotic-AUR
echo "Importing the primary key for Chaotic-AUR..."
sudo pacman-key --recv-key 3056513887B78AEB --keyserver keyserver.ubuntu.com
sudo pacman-key --lsign-key 3056513887B78AEB

# Install Chaotic-AUR keyring and mirrorlist
echo "Installing Chaotic-AUR keyring and mirrorlist..."
sudo pacman -U --noconfirm 'https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-keyring.pkg.tar.zst' \
'https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-mirrorlist.pkg.tar.zst'

# Step 5: Append Chaotic-AUR to pacman.conf if not already added
if ! grep -q "\[chaotic-aur\]" /etc/pacman.conf; then
    echo "Adding Chaotic-AUR repository to pacman.conf..."
    echo "[chaotic-aur]" | sudo tee -a /etc/pacman.conf
    echo "Include = /etc/pacman.d/chaotic-mirrorlist" | sudo tee -a /etc/pacman.conf
fi

# Step 6: Refresh the pacman database
echo "Refreshing pacman database..."
sudo pacman -Sy

# Step 7: Install AUR and Chaotic-AUR packages using paru (add your desired packages here)
echo "Installing AUR and Chaotic-AUR packages..."

# Step 8: Clean up cache if necessary
echo "Cleaning up package cache..."
paru -Sc --noconfirm

echo "Paru and Chaotic-AUR setup and package installation completed!"
