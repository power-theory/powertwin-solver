############################################################################################################
# accuracy.py
# This script compares predicted and actual consumption data, calculates the accuracy, and 
#   plots the results. The script reads two CSV files containing predicted and actual 
#   consumption data, respectively. It then compares the data within a specified date range, 
#   calculates the accuracy, and saves the results to a new CSV file. The script also calculates the 
#   average, month-to-month, day, hour, day-hour, and month-day-hour accuracy, which are then plotted 
#   for visualization.
############################################################################################################


import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import argparse
import os


############################################################################################################
# Name: calculate_accuracy(actual, predicted)
# Description: Function to calculate accuracy using the formula: 
#               100 - ((|Actual - Predicted| / Actual) × 100)
#   The actual parameter is the actual value, and the predicted parameter is the predicted value
############################################################################################################
def calculate_accuracy(actual, predicted):
    if actual == 0:  # Division by zero case
        return 100 if predicted == 0 else 0
    return 100 - (abs(actual - predicted) / abs(actual)) * 100

############################################################################################################
# Name: validate_dates_and_range(start_date, end_date, data_df)
# Description: Function to validate the start_date and end_date are within the range of the actual data.
#   The start_date and end_date parameters are the date range to validate, and the data_df parameter is
#   the DataFrame containing the actual data.
############################################################################################################
def validate_date_range(start_date, end_date, actual_df):
    # Convert start_date and end_date to Timestamp objects
    start_date = pd.to_datetime(start_date, format='%Y-%m-%dT%H:%M:%S')
    end_date = pd.to_datetime(end_date, format='%Y-%m-%dT%H:%M:%S')

    # Ensure the start_date and end_date are within the range of the actual data
    if start_date < actual_df['timestamp'].min() or end_date > actual_df['timestamp'].max():
        raise ValueError("ERROR: IMPROPER DATE RANGE")
    

############################################################################################################
# Name: get_accuracy(predicted_csv, actual_csv, output_csv, start_date, end_date)
# Description: Function to compare predicted and actual consumption data, calculate the accuracy,
#   and save the results to a new CSV file. The start_date and end_date parameters specify the date range
#   for filtering the data. The predicted_csv and actual_csv parameters are the paths to the predicted and
#   actual CSV files, respectively. The output_csv parameter is the path to save the accuracy results.
############################################################################################################
def get_accuracy(predicted_csv, actual_csv, output_dir, start_date, end_date):
    
    # Read the predicted CSV 
    predicted_df = pd.read_csv(predicted_csv, usecols=['ts', 'value'])
    predicted_df['timestamp'] = pd.to_datetime(predicted_df['ts'], format='%Y-%m-%dT%H:%M:%S')  

    # Read the actual CSV 
    actual_df = pd.read_csv(actual_csv, usecols=['ts', 'value'])
    actual_df['timestamp'] = pd.to_datetime(actual_df['ts'], format='%Y-%m-%dT%H:%M:%S')  

    validate_date_range(start_date, end_date, actual_df)

    # Filter data based on the date range
    predicted_df = predicted_df[(predicted_df['timestamp'] >= start_date) & (predicted_df['timestamp'] <= end_date)]
    actual_df = actual_df[(actual_df['timestamp'] >= start_date) & (actual_df['timestamp'] <= end_date)]

    # Merge the predicted and actual data on the timestamp
    merged_df = pd.merge(predicted_df, actual_df, on='timestamp', suffixes=('_predicted', '_actual'))

    # Calculate the accuracy
    merged_df['accuracy'] = merged_df.apply(lambda row: calculate_accuracy(row['value_actual'], row['value_predicted']), axis=1)



    # Reorder columns to match the desired format
    merged_df = merged_df[['timestamp', 'value_predicted', 'value_actual', 'accuracy']]

    # Format the timestamp column to include the 'T' separator
    merged_df['timestamp'] = merged_df['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S')

    # Ensure the output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Save the results to the output CSV file
    output_file = os.path.join(output_dir, 'electricity_accuracy_results.csv')
    merged_df.to_csv(output_file, index=False)


############################################################################################################
# Name: plot_accuracy(data, output_dir)
# Description: Function to plot the month-to-month accuracy and the average accuracy meter along with
#   a heatmap of day-hour accuracy. The data parameter is a DataFrame containing the accuracy results.
#   The function creates and returns the path to the analyzed results CSV file which has a comprehensive
#   collection of the accuracy measurements.
############################################################################################################
def plot_accuracy(data, output_dir):
 
    plt.rcParams['font.size'] = 12  
    
    average_accuracy = data['accuracy'].mean()   
    
    data['timestamp'] = pd.to_datetime(data['timestamp'])
    
    # Extract month and month name from the timestamp
    data['month'] = data['timestamp'].dt.to_period('M')
    data['month_name'] = data['timestamp'].dt.strftime('%b-%Y')
    
    # Extract the day of the week and hour 
    data['day_of_week'] = data['timestamp'].dt.dayofweek
    data['hour'] = data['timestamp'].dt.hour
    
    month_accuracy = data.groupby('month_name')['accuracy'].mean()
    month_accuracy = month_accuracy.sort_index(key=lambda x: pd.to_datetime(x, format='%b-%Y'))
            
    # Create subplots
    fig, axs = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [1, 4]})

    ########################################################
    # Plot average accuracy as a horizontal line graph
    ########################################################
    axs[0].barh([''], [100], color='gray', height=0.3)
    axs[0].barh([''], [average_accuracy], color='darkgreen' if average_accuracy >= 50 else 'darkred', height=0.3) 

    axs[0].set_xlim(0, 100)
    axs[0].set_title('Average Accuracy Meter')
    axs[0].set_xlabel('Accuracy (%)')

    # Add value as a label on the bar
    axs[0].text(average_accuracy, 0, f'{average_accuracy:.2f}%', ha='center', va='center', color='black')
    axs[0].get_yaxis().set_visible(False)

    ########################################################
    # Plot month-to-month accuracy as a bar graph
    ########################################################
    colors = ['darkred' if value < 50 else 'darkblue' for value in month_accuracy]
    month_accuracy = month_accuracy.apply(lambda x: max(0, x))
    month_accuracy.plot(kind='bar', color=colors, ax=axs[1])
    
    axs[1].set_title('Month-to-Month Accuracy')
    axs[1].set_xlabel('Month')
    axs[1].set_ylabel('Accuracy (%)')
    axs[1].set_xticklabels(month_accuracy.index, rotation=45)
    axs[1].set_ylim(0, 100)

    # Add values on top of each bar
    for i, value in enumerate(month_accuracy):
        axs[1].text(i, value + 0.5, f'{value:.2f}', ha='center', va='bottom')


    plt.tight_layout()
    plt.subplots_adjust(hspace=0.5) 

    # Save the plot as a PNG file
    accuracy_plot_file = os.path.join(output_dir, 'accuracy_plot.png')
    plt.savefig(accuracy_plot_file)
    plt.close()

    
    ########################################
    # Plot Hour-Range Accuracy as a Bar Graph
    ########################################
    hour_bins = pd.cut(data['hour'], bins=[0, 3, 6, 9, 12, 15, 18, 21, 24], right=False, labels=['0-3', '3-6', '6-9', '9-12', '12-15', '15-18', '18-21', '21-24'])
    hour_range_accuracy = data.groupby(hour_bins, observed=True)['accuracy'].mean()

    # Plot hour-range accuracy with bars representing the average accuracy per range
    plt.figure(figsize=(8, 6))
    hour_range_accuracy = hour_range_accuracy.apply(lambda x: max(0, x))
    plt.bar(hour_range_accuracy.index, hour_range_accuracy.values, color='darkblue', edgecolor='black')
    plt.title('Average Accuracy by Hour Range')
    plt.xlabel('Hour Range')
    plt.ylabel('Average Accuracy (%)')
    plt.ylim(0, 100)
    for i, value in enumerate(hour_range_accuracy):
        plt.text(i, value + 1, f'{value:.2f}%', ha='center', va='bottom', color='black')

    plt.tight_layout()
    
    # Save the plot as a PNG file
    hour_range_plot_file = os.path.join(output_dir, 'hour-range_plot.png')
    plt.savefig(hour_range_plot_file)
    plt.close()
    
    ########################################
    # Plot Day-Hour Accuracy as a HeatMap
    ########################################
    day_mapping = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
    data['day_name'] = data['day_of_week'].map(day_mapping)

    # Create a pivot table for day of week and hour with average accuracy
    pivot_table = data.pivot_table(values='accuracy', index='day_name', columns='hour', aggfunc='mean').reindex(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday','Sunday'])

    # Plot the heatmap
    plt.figure(figsize=(14, 10))
    sns.heatmap(pivot_table, cmap="Reds", annot=True, fmt=".2f", linewidths=.5, vmin=0, vmax=100,
                annot_kws={"size": 8})  # Set annotation text size to 8

    plt.title('Average Accuracy Heatmap by Day and Hour')
    plt.xlabel('Hour of the Day')
    plt.ylabel('Day of the Week')
    plt.tight_layout()
    
    # Save the plot as a PNG file
    heatmap_plot_file = os.path.join(output_dir, 'day-hour_heatmap.png')
    plt.savefig(heatmap_plot_file)
    plt.close()


    ########################################
    # Prepare CSV Data
    ########################################
    day_avg_accuracy = data.groupby('day_name')['accuracy'].mean().reset_index(name='Day Average Accuracy')
    hour_avg_accuracy = data.groupby('hour')['accuracy'].mean().reset_index(name='Hour Average Accuracy')
    day_hour_avg_accuracy = data.groupby(['day_name', 'hour'])['accuracy'].mean().reset_index(name='Day-Hour Accuracy')

    # Save the accuracy data (monthly, daily, hourly, and combinations) into a CSV
    accuracy_data = pd.DataFrame({
        'Month': month_accuracy.index,
        'Monthly Accuracy': month_accuracy.values
    })
    
    # Add overall accuracy at the end
    accuracy_data.loc[len(accuracy_data)] = ['Overall Accuracy', average_accuracy]

    # Merge all accuracy results into one DataFrame
    merged_accuracy_data = pd.concat([
        day_avg_accuracy, hour_avg_accuracy, day_hour_avg_accuracy, accuracy_data
    ], axis=1)

    # Save to CSV
    output_file = os.path.join(output_dir, 'electricity_analyzed_results.csv')
    merged_accuracy_data.to_csv(output_file, index=False)

############################################################################################################
# Name: plot_monthdayhour_heatmap(data)
# Description: Function to plot a grid of heatmaps showing the average accuracy by month, day, and hour.
#   The data parameter is a DataFrame containing the accuracy results.
############################################################################################################
def plot_monthdayhour_heatmap(data, output_dir):
    # Convert timestamp to datetime for month, day, and hour extraction
    data['timestamp'] = pd.to_datetime(data['timestamp'])
    data['month_name'] = data['timestamp'].dt.strftime('%b')
    data['day_of_week'] = data['timestamp'].dt.dayofweek
    data['hour'] = data['timestamp'].dt.hour

    # Define the correct order of months (sorted by year first, then month)
    data['month_order'] = pd.to_datetime(data['timestamp']).dt.strftime('%Y-%m')

    # Create a pivot table to prepare data for heatmaps
    pivot_table = data.pivot_table(values='accuracy', index=['month_order', 'day_of_week', 'hour'], aggfunc='mean').reset_index()

    # Sort by month_order to ensure correct chronological order
    pivot_table = pivot_table.sort_values('month_order')

    # Create a grid of heatmaps with one heatmap per month
    g = sns.FacetGrid(pivot_table, col="month_order", col_wrap=3, height=4, aspect=1.5, sharex=False, sharey=False)

    # Create heatmap for each month
    g.map_dataframe(
        lambda data, color, **kwargs: sns.heatmap(
            data.pivot(index='day_of_week', columns='hour', values='accuracy'),
            cmap="Reds", cbar=True, linewidths=.5, annot=False, vmin=0, vmax=100, **kwargs
        ), data=pivot_table)

    for ax in g.axes.flat:
        ax.xaxis.set_visible(False)

    g.set_titles(col_template='{col_name}', size=12)
    g.set_axis_labels('Hour of the Day', 'Day of the Week')
    plt.subplots_adjust(top=0.9)
    g.figure.suptitle('Average Accuracy by Month, Day, and Hour', fontsize=16)

    heatmap_grid_plot_file = os.path.join(output_dir, 'mdh_accuracy_heatmap.png')
    g.savefig(heatmap_grid_plot_file)
    plt.close()
    


############################################################################################################
# Main script execution: Compare CSV files, calculate accuracy, and plot the results
############################################################################################################
if __name__ == "__main__":
    # Set up argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument('predicted_csv', type=str)
    parser.add_argument('actual_csv', type=str)
    parser.add_argument('output_dir', type=str)
    parser.add_argument('start_date', type=str)
    parser.add_argument('end_date', type=str)

    
    # Parse arguments
    args = parser.parse_args()
    
    #TODO: ADJUST SO THAT ARGPARSE IS NO LONGER NEEDED (POTENTIALLY CREATE A MAIN FUNCTION)

   # Check if the actual_csv file exists
    if not os.path.exists(args.actual_csv):
        print(f"File {args.actual_csv} does not exist. Skipping comparison.")
    else:
        # Call the function with parsed arguments
        get_accuracy(args.predicted_csv, args.actual_csv, args.output_dir, args.start_date, args.end_date)

        # Analyze accuracy results
        cleaned_results_csv = f'{args.output_dir}/electricity_accuracy_results.csv'
        data = pd.read_csv(cleaned_results_csv)
        plot_accuracy(data, args.output_dir)  # Uncomment to plot accuracy
        plot_monthdayhour_heatmap(data, args.output_dir)  # Uncomment to plot month-day-hour heatmap
