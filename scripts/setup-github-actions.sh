#!/bin/bash

# Setup GitHub Actions for Vietnam Hearts project
echo "🚀 Setting up GitHub Actions for Vietnam Hearts"

# Get the current directory name (should be the repo name)
REPO_NAME=$(basename "$PWD")

# Ask for GitHub username
echo "Please enter your GitHub username:"
read GITHUB_USERNAME

if [ -z "$GITHUB_USERNAME" ]; then
    echo "❌ GitHub username is required"
    exit 1
fi

# Update the badge in README.md
echo "📝 Updating README.md with your GitHub username..."
sed -i "s/YOUR_USERNAME/$GITHUB_USERNAME/g" README.md

echo "✅ GitHub Actions setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Push your code to GitHub:"
echo "   git add ."
echo "   git commit -m 'Add GitHub Actions workflow'"
echo "   git push origin main"
echo ""
echo "2. Check the Actions tab on GitHub to see your tests running"
echo "3. The badge in README.md will show the test status"
echo ""
echo "💰 Cost: FREE! GitHub Actions provides:"
echo "   - Public repos: Unlimited minutes"
echo "   - Private repos: 2,000 minutes/month free"
echo "   - Each test run: ~1-2 minutes"
echo "   - That's ~1,000 test runs per month for free!" 